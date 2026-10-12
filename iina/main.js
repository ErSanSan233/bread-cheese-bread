/**
 * Main 入口 —— 每个播放器窗口一个实例。播放器相关的能力只有这里能用：
 * iina.core 的数据（当前文件路径、播放位置）在 Global 入口取不到。
 *
 * 探针阶段 v3。已确认的事实：
 *   - core.status 可读：url / position / duration / paused，OSD 可用
 *   - Global 入口里 http 能连本机、exec 能跑 Python/ffmpeg、后台进程能存活
 *   - webview 页面里的 fetch / XHR 能直连本机后端（后端访问日志已证实）
 * 尚未确认：
 *   - 页面 → 插件的消息桥（postMessage）接不通。本文件试着从 Main 入口开窗口，
 *     验证这座桥是否只对 Main 生效。这是最后一环，决定界面怎么传参。
 */

const OUT_PATH = "/tmp/bcb-iina-probe-main.json";
const WW_FLAG = "@data/webview-main.json";

function now() {
  return new Date().toISOString();
}

async function probe(label, fn) {
  try {
    const value = await fn();
    return { ok: true, value: value };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
}

function resolve(p) {
  try {
    return iina.utils.resolvePath(p);
  } catch (e) {
    return p;
  }
}

function writeTo(path, obj) {
  try {
    iina.file.write(resolve(path), JSON.stringify(obj, null, 2));
    return true;
  } catch (e) {
    return false;
  }
}

function toPath(url) {
  if (!url) return null;
  if (url.indexOf("file://") === 0) {
    try {
      return decodeURIComponent(url.slice("file://".length));
    } catch (e) {
      return url;
    }
  }
  return url;
}

function snapshot() {
  const s = iina.core.status;
  return {
    url: s.url,
    path: toPath(s.url),
    title: s.title,
    position: s.position,
    duration: s.duration,
    paused: s.paused,
    idle: s.idle,
    isNetworkResource: s.isNetworkResource,
  };
}

/** 从 Main 入口开同一个测试窗口：顺便把当前文件路径和播放位置拼在 URL 上传进去。 */
function openProbeWindow() {
  let snap = null;
  try {
    snap = snapshot();
  } catch (e) {
    /* 忽略 */
  }
  // 走 URL 传参这条路，是因为「页面 → 插件」的消息桥在本机型（1.5.0 build 180）上
  // 始终没通；而 URL 参数不依赖任何注入脚本。
  const query =
    "file=" + encodeURIComponent((snap && snap.path) || "") + "&pos=" + ((snap && snap.position) || 0);
  try {
    iina.standaloneWindow.setProperty({ title: "BCB 桥测试（Main）", resizable: true });
    iina.standaloneWindow.setFrame(560, 380);
    iina.standaloneWindow.onMessage("probeResult", function (data) {
      writeTo(WW_FLAG, { stage: "Main 收到 webview 消息", data: data, time: now() });
      try {
        iina.core.osd("BCB：webview 回报成功");
      } catch (e) {
        /* 忽略 */
      }
    });
    iina.standaloneWindow.loadFile("probe.html?" + query);
    iina.standaloneWindow.open();
    writeTo(WW_FLAG, { stage: "Main 已开窗口，等待回报", 传入查询串: query, time: now() });
  } catch (e) {
    writeTo(WW_FLAG, { stage: "Main 开窗口失败", error: String(e), time: now() });
  }
}

async function runProbe() {
  const r = { scope: "main", time: now(), version: null, status: null, osd: null, mpv: null };

  r.version = await probe("core.getVersion", function () {
    return iina.core.getVersion();
  });
  r.status = await probe("core.status", function () {
    return snapshot();
  });
  r.osd = await probe("core.osd", async function () {
    iina.core.osd("两面包夹芝士：探针就绪");
    return "未抛错";
  });
  r.mpv = await probe("mpv time-pos", async function () {
    return {
      timePos: iina.mpv.getNumber("time-pos"),
      mpvPath: iina.mpv.getString("path"),
    };
  });
  return r;
}

async function main() {
  const results = await runProbe();
  writeTo(OUT_PATH, results);
  writeTo("@data/probe-main.json", results);
  iina.console.log("[bcb] Main 探针完成");

  if (iina.menu) {
    iina.menu.addItem(
      iina.menu.item("🧪 探针：记录当前文件与播放位置", function () {
        let snap = null;
        try {
          snap = snapshot();
        } catch (e) {
          iina.console.error("[bcb] 读播放状态失败：" + e);
        }
        writeTo("@data/status-manual.json", { time: now(), status: snap });
        try {
          const p = snap ? snap.position : null;
          iina.core.osd(
            "已记录：" +
              ((snap && (snap.title || snap.path)) || "无文件") +
              " @ " +
              (p === null ? "无位置" : Math.round(p * 10) / 10 + "s"),
          );
        } catch (e) {
          iina.console.error("[bcb] OSD 失败：" + e);
        }
      }),
    );
    iina.menu.addItem(iina.menu.item("🧪 测试 WebView 桥（Main）", openProbeWindow));
  }

  // 首次自动跑一次，之后不再打扰
  let need = true;
  try {
    need = !iina.file.exists(resolve(WW_FLAG));
  } catch (e) {
    need = true;
  }
  if (need) {
    setTimeout(openProbeWindow, 1500);
  }
}

main();
