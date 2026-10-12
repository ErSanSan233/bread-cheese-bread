/**
 * Global 入口 —— 随 IINA 启动执行一次，负责「进程级」的工作：环境探测与后端服务。
 *
 * 探针阶段 v2。上一轮已经确认的核心事实（见 docs/iina-probe.md）：
 *   - iina.http 可以访问本机回环（127.0.0.1 / localhost 都要写进 allowedDomains）
 *   - 未登记的域名会被明确拒绝：”URL xxx is not allowed.”
 *   - iina.utils.exec 能跑绝对路径的 Python
 *   - 插件进程的 PATH 里没有 /opt/homebrew/bin，fileInPath("ffmpeg") 为 false
 *   - @data 可写；写 /tmp 时灵时不灵
 *
 * 本轮要收口的三个问题：
 *   1. exec 起的后台进程能否在调用返回后继续存活（决定能不能从插件拉起常驻后端）
 *   2. 用 login shell 跑命令能否拿到完整 PATH（决定 ffmpeg 怎么调用）
 *   3. webview 里的 fetch 能否直连本机服务（决定现有 web/ 界面能不能原样复用）
 */

const OUT_REL = "@data/probe-global.json";
const TMP_PATH = "/tmp/bcb-iina-probe-global.json";

function now() {
  return new Date().toISOString();
}

function sleep(ms) {
  return new Promise(function (resolve) {
    setTimeout(resolve, ms);
  });
}

function withTimeout(promise, ms) {
  return Promise.race([
    promise,
    sleep(ms).then(function () {
      return { __timeout: true };
    }),
  ]);
}

async function probe(label, fn) {
  try {
    const value = await fn();
    return { ok: true, value: value };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
}

function port() {
  try {
    return String(iina.preferences.get("port") || "8733");
  } catch (e) {
    return "8733";
  }
}

function writeOut(obj) {
  // @data 作为主落点最稳；/tmp 顺带试一下，把成败原因也记进来。
  const report = { 主落点: null, tmp: null };
  let text;
  try {
    text = JSON.stringify(obj, null, 2);
  } catch (e) {
    report.序列化失败 = String(e);
    try {
      iina.file.write(iina.utils.resolvePath(OUT_REL), JSON.stringify({ 序列化失败: String(e) }));
    } catch (e2) {
      /* 兜底也失败了就算了 */
    }
    return report;
  }
  try {
    const p = iina.utils.resolvePath(OUT_REL);
    iina.file.write(p, text);
    report.主落点 = p;
  } catch (e) {
    report.主落点 = "失败：" + String(e);
  }
  try {
    iina.file.write(TMP_PATH, text);
    report.tmp = TMP_PATH;
  } catch (e) {
    report.tmp = "失败：" + String(e);
  }
  return report;
}

/** 探针 1：后台进程是否存活 —— 用「子进程隔几秒写个文件」来判定，不依赖 pgrep。 */
async function probeDetach() {
  const marker = iina.utils.resolvePath("@data/detach-marker.txt");
  const markerAbs = marker;
  try {
    if (iina.file.exists(marker)) iina.file.delete(marker);
  } catch (e) {
    /* 忽略 */
  }
  const cmd =
    "nohup /bin/bash -c 'sleep 5; echo alive > \"" + markerAbs + "\"' > /dev/null 2>&1 & echo spawned";
  const spawn = await withTimeout(iina.utils.exec("/bin/bash", ["-c", cmd]), 6000);
  const spawnInfo = spawn && spawn.__timeout ? "exec 超时" : String(spawn.stdout || "").trim();
  await sleep(7000);
  let exists = false;
  let content = null;
  try {
    exists = iina.file.exists(marker);
    if (exists) content = String(iina.file.read(marker)).trim();
  } catch (e) {
    content = "读取失败：" + String(e);
  }
  return {
    后台进程是否写入标记: exists,
    标记内容: content,
    spawn输出: spawnInfo,
  };
}

async function runProbe() {
  const p = port();
  const r = {
    scope: "global",
    time: now(),
    http: {},
    exec: {},
    detach: null,
    pathEnv: {},
  };

  // ---- 回环连通性（上一轮已验证，保留作为回归）----
  r.http["127.0.0.1"] = await probe("health", async function () {
    const res = await withTimeout(iina.http.get("http://127.0.0.1:" + p + "/api/health", {}), 8000);
    if (res && res.__timeout) return "超时";
    return String(res.text).slice(0, 160);
  });

  // ---- PATH 环境 ----
  r.pathEnv["插件进程 PATH"] = await probe("PATH", async function () {
    const res = await withTimeout(iina.utils.exec("/bin/bash", ["-c", "echo $PATH"]), 6000);
    return String(res.stdout).trim();
  });

  r.pathEnv["login shell PATH"] = await probe("PATH -l", async function () {
    const res = await withTimeout(iina.utils.exec("/bin/bash", ["-lc", "echo $PATH"]), 6000);
    return String(res.stdout).trim();
  });

  r.pathEnv["login shell 找 ffmpeg"] = await probe("which ffmpeg -l", async function () {
    const res = await withTimeout(
      iina.utils.exec("/bin/bash", ["-lc", "command -v ffmpeg || echo NOT_FOUND"]),
      6000,
    );
    return String(res.stdout).trim();
  });

  // ---- 绝对路径调用 ----
  r.exec["ffmpeg 绝对路径"] = await probe("/opt/homebrew/bin/ffmpeg", async function () {
    const res = await withTimeout(
      iina.utils.exec("/opt/homebrew/bin/ffmpeg", ["-version"]),
      8000,
    );
    if (res && res.__timeout) return "超时";
    return {
      status: res.status,
      第一行: String(res.stdout).split("\n")[0].slice(0, 120),
    };
  });

  // ---- 后台进程 ----
  r.detach = await probe("detach", probeDetach);

  return r;
}

const WEBVIEW_FLAG = "@data/webview-probe.json";

/** 每步都写一次标记，便于定位卡在哪一环。 */
function writeStage(stage, extra) {
  const rec = { stage: stage, time: now() };
  if (extra) {
    Object.keys(extra).forEach(function (k) {
      rec[k] = extra[k];
    });
  }
  try {
    iina.file.write(iina.utils.resolvePath(WEBVIEW_FLAG), JSON.stringify(rec, null, 2));
  } catch (e) {
    iina.console.error("[bcb] 写阶段标记失败：" + e);
  }
}

/** 只在第一次跑（用标记文件判断），避免每次启动都弹窗口烦人。 */
function maybeRunWebviewProbe(base) {
  let need = true;
  try {
    need = !iina.file.exists(iina.utils.resolvePath(WEBVIEW_FLAG));
  } catch (e) {
    need = true;
  }
  if (!need) return;

  writeStage("scheduled");
  setTimeout(function () {
    writeStage("setTimeout fired");
    try {
      iina.standaloneWindow.setProperty({ title: "BCB 连通性测试", resizable: true });
      iina.standaloneWindow.setFrame(520, 320);
      iina.standaloneWindow.onMessage("probeResult", function (data) {
        const all = Object.assign({}, base, { webview: data, webviewTime: now() });
        writeStage("done", all);
      });
      iina.standaloneWindow.loadFile("probe.html");
      iina.standaloneWindow.open();
      writeStage("window opened, waiting webview");
    } catch (e) {
      iina.console.error("[bcb] 打开测试窗口失败：" + e);
      writeStage("open failed", { error: String(e) });
    }
  }, 1500);
}

async function main() {
  const results = await runProbe();
  const io = writeOut(results);
  iina.console.log("[bcb] 探针完成 " + JSON.stringify(io));
  maybeRunWebviewProbe(results);

  if (iina.menu) {
    iina.menu.addItem(
      iina.menu.item("🧪 重跑环境探针", function () {
        runProbe().then(function (r2) {
          writeOut(r2);
        });
      }),
    );
    iina.menu.addItem(
      iina.menu.item("🧪 重测 WebView 连通性", function () {
        try {
          const flag = iina.utils.resolvePath(WEBVIEW_FLAG);
          if (iina.file.exists(flag)) iina.file.delete(flag);
        } catch (e) {
          /* 忽略 */
        }
        maybeRunWebviewProbe(results);
      }),
    );
  }
}

main();
