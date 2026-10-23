/**
 * Main 入口 —— 每个播放器窗口一个实例。播放器相关的能力只有这里能用：
 * iina.core / iina.mpv / iina.overlay / iina.sidebar 都属于窗口。
 *
 * 界面形态：主面板是 iina.sidebar，嵌在播放器右侧；即时提示是 iina.overlay，
 * 浮在画面上。全程不依赖外部浏览器。
 *
 * 与面板的通信走官方消息桥（iina.sidebar.postMessage / window.iina.postMessage），
 * 即时双向，不用轮询，也不经过后端。后端只负责合成这类重活。
 *
 * 唯一要小心的是注册时机：sidebar.loadFile 内部会把已注册的监听全部清掉，
 * 所以 onMessage 必须排在 loadFile 之后，详见 bindPanelMessages 的注释。
 */

const PORT = 8733;
const panel = iina.sidebar;

/** 当前三个打点，null 表示未设置。A < B < C。 */
let marks = { a: null, b: null, c: null };
let overlayTimer = null;
let gotUiReady = false;
let windowReady = false;
let panelLoaded = false;
let panelShown = false;
// 面板通过官方桥报到过 = 双向可用。之后状态直接推给页面，不再绕后端
let bridgeUp = false;

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

function currentMedia() {
  let file = null, path = null, dur = 0;
  try {
    const s = iina.core.status;
    file = s.title || toPath(s.url) || null;
    path = toPath(s.url);
    dur = s.duration || 0;
  } catch (e) {
    /* 播放器窗口可能还没就绪 */
  }
  // 刚载入文件时 core.status 的时长常常还没算出来，拿 mpv 兜一层
  if (!dur) {
    try {
      dur = iina.mpv.getNumber("duration") || 0;
    } catch (e) {
      /* 忽略 */
    }
  }
  return { file: file, path: path, dur: dur };
}

function currentPos() {
  try {
    return iina.core.status.position || iina.mpv.getNumber("time-pos") || 0;
  } catch (e) {
    try {
      return iina.mpv.getNumber("time-pos") || 0;
    } catch (e2) {
      return 0;
    }
  }
}

/**
 * 视频画面上的短提示。overlay 完全由插件驱动，不依赖与网页通信，
 * 所以即便面板还没打开，提示照样能显示。
 */
function flash(message, isError) {
  try {
    iina.overlay.simpleMode();
    iina.overlay.setStyle(
      "html,body{margin:0;padding:0;background:transparent!important;overflow:hidden}" +
        "p{position:absolute;left:0;right:0;bottom:56px;text-align:center;" +
        "font:500 15px -apple-system,'PingFang SC',sans-serif;" +
        "color:#fff;text-shadow:0 1px 4px rgba(0,0,0,.9);margin:0;padding:0 20px}",
    );
    iina.overlay.setContent(
      "<p style='color:" + (isError ? "#ff9c93" : "#ffffff") + "'>" + message + "</p>",
    );
    iina.overlay.show();
    if (overlayTimer) clearTimeout(overlayTimer);
    overlayTimer = setTimeout(function () {
      try {
        iina.overlay.hide();
      } catch (e) {
        /* 窗口可能已关闭 */
      }
    }, 2200);
  } catch (e) {
    try {
      iina.core.osd(message);
    } catch (e2) {
      /* 放弃 */
    }
  }
}

/* ---------- 与后端通信 ---------- */

function postSession(payload) {
  try {
    iina.http.post("http://127.0.0.1:" + PORT + "/api/session", {
      data: payload,
      headers: { "Content-Type": "application/json" },
    });
  } catch (e) {
    /* 后端没启动时静默，不打扰正在看片子的人 */
  }
}

function getSession(cb) {
  try {
    iina.http.get("http://127.0.0.1:" + PORT + "/api/session", {}).then(
      function (res) {
        try {
          cb(JSON.parse(res.text || "{}"));
        } catch (e) {
          /* 解析不动就当没这回事 */
        }
      },
      function () {
        /* 后端未启动，忽略 */
      },
    );
  } catch (e) {
    /* 忽略 */
  }
}

function pushState() {
  const m = currentMedia();
  if (!m.path) return;
  postSession({ path: m.path, name: m.file, pos: currentPos(), dur: m.dur, alive: true });
}

function pushMarks() {
  postSession({ marks: marks });
}

/**
 * 让面板代显示一句话。
 *
 * 关键约束：`iina.http` 的回调跑在 NSURLSession 的后台队列上，
 * 在这个队列里碰任何播放器 UI（overlay / OSD / sidebar）都会让 IINA 直接
 * abort —— 崩溃栈是 overlay 首次创建容器时的 addSubview 抛异常。
 * 因此凡是从 http 回调里发起的动作，反馈一律走后端，由面板渲染。
 */
function notify(text, isError) {
  const payload = { text: text, is_error: !!isError, at: Date.now() / 1000 };
  if (sayToPanel("notice", payload)) return;
  postSession({ notice: payload });
}

/**
 * 推一条消息给面板。走的是官方桥（iina.sidebar.postMessage，内部是主线程上的
 * evaluateJavaScript），所以既即时又不碰网络。返回 false 表示桥还没通。
 */
function sayToPanel(name, data) {
  if (!bridgeUp) return false;
  try {
    panel.postMessage(name, data);
    return true;
  } catch (e) {
    return false;
  }
}

function pushStateToPanel() {
  const m = currentMedia();
  sayToPanel("state", {
    path: m.path, name: m.file, pos: currentPos(), dur: m.dur,
    marks: { a: marks.a, b: marks.b, c: marks.c },
  });
}

/** 状态或打点变了，按当前通道同步出去（桥优先，退回后端会话）。 */
function syncOut() {
  if (bridgeUp) {
    pushStateToPanel();
    return;
  }
  pushState();
  pushMarks();
}

function syncMarksOut() {
  if (bridgeUp) {
    sayToPanel("marks", marks);
    return;
  }
  pushMarks();
}

function togglePause() {
  try {
    if (iina.core.status.paused) iina.core.resume();
    else iina.core.pause();
  } catch (e) {
    /* 忽略 */
  }
}

/**
 * 面板上的按钮把打点写进会话，快捷键则写在插件本地。
 * 两边可能各自只写了一半（例如点按钮打 A、按快捷键打 C），
 * 所以每轮都以会话为准补进本地 —— 否则本地缺失时试听会误判为「还没打点」。
 */
function syncMarks(session) {
  const s = session && session.marks;
  if (!s) return;
  ["a", "b", "c"].forEach(function (k) {
    if (s[k] === undefined) return;
    if (s[k] !== marks[k]) {
      marks[k] = s[k];
      sayToPanel("marks", marks);
    }
  });
}

/**
 * 面板已经自己把稿子送上去了，这里把面板下的指令捞回来执行。
 *
 * 每条指令都加了最小间隔：清零指令的那次 POST 可能没送达（后端没起来、
 * 或者请求丢了），那样同一条指令会在下一轮又被读成「还没执行」，
 * 变成每 800 毫秒跳一次、播一次的鬼畜循环。
 */
const cmdAt = { preview: 0, goto: 0, toggle: 0 };
const CMD_GAP_MS = { preview: 2500, goto: 800, toggle: 350 };

function onceInAWhile(kind) {
  const now = Date.now();
  if (now - cmdAt[kind] < CMD_GAP_MS[kind]) return false;
  cmdAt[kind] = now;
  return true;
}

function drainPanelCommands() {
  getSession(function (d) {
    syncMarks(d);
    if (d.want_pause_toggle) {
      postSession({ want_pause_toggle: false });
      if (onceInAWhile("toggle")) {
        // 焦点还在面板里时，空格到不了播放器，这里替它按一下
        try {
          if (iina.core.status.paused) iina.core.resume();
          else iina.core.pause();
        } catch (e) {
          /* 忽略 */
        }
      }
    }
    if (d.want_preview) {
      postSession({ want_preview: false });
      if (onceInAWhile("preview")) previewRef(true);   // 处于 http 回调内，禁止触碰播放器 UI
    }
    if (d.want_goto !== undefined && d.want_goto !== null) {
      const t = d.want_goto;
      postSession({ want_goto: null });
      if (onceInAWhile("goto")) {
        try {
          iina.mpv.set("time-pos", t);
        } catch (e) {
          /* 忽略 */
        }
      }
    }
  });
}

/* ---------- 打点 ---------- */

/**
 * 打点。B 必须落在 A 与 C 之间，否则拒绝并给出理由。
 *
 * quiet 为真表示这次是从面板的消息回调里发起的，反馈要送回面板；
 * 从菜单 / 快捷键发起时直接上画面浮层。
 */
function setMark(role, quiet) {
  const t = currentPos();
  const key = role.toLowerCase();
  const say = quiet ? notify : function (text, isError) { flash(text, isError); };

  if (key === "a" && marks.c !== null && t >= marks.c) {
    say("A 要早于 C（当前在 C 之后）", true);
    return;
  }
  if (key === "c" && marks.a !== null && t <= marks.a) {
    say("C 要晚于 A（当前在 A 之前）", true);
    return;
  }
  if (key === "b") {
    if (marks.a !== null && t <= marks.a) {
      say("B 要落在 A 与 C 之间", true);
      return;
    }
    if (marks.c !== null && t >= marks.c) {
      say("B 要落在 A 与 C 之间", true);
      return;
    }
  }

  marks[key] = t;
  syncMarksOut();
  const label = { a: "面包起点 A", b: "插入点 B", c: "面包终点 C" }[key];
  say(label + " → " + t.toFixed(1) + "s");
}

function clearMarks(quiet) {
  marks = { a: null, b: null, c: null };
  syncMarksOut();
  const say = quiet ? notify : function (text) { flash(text); };
  say("已清空打点");
}

/**
 * 播放 A→C，让用户亲耳确认旁白会照这段的语气来。
 *
 * quiet 为 true 表示这次调用发生在 http 回调里（面板下的指令），
 * 此时不能碰播放器 UI，反馈改用 notify 交给面板。
 */
function previewRef(quiet) {
  const say = quiet
    ? notify
    : function (t, e) {
        flash(t, e);
      };
  if (marks.a === null || marks.c === null) {
    say("先打上 A 和 C", true);
    return;
  }
  try {
    iina.mpv.set("time-pos", marks.a);
    iina.core.resume();
    say(quiet ? "正在播放 A→C" : "正在播放 A→C，这就是参考语音", false);
  } catch (e) {
    say("跳转失败：" + e, true);
  }
}

/* ---------- 面板的显示与焦点 ---------- */

/**
 * 侧栏是一个 WKWebView。它一旦拿到 first responder，空格这类不带修饰键的按键
 * 就落进网页里（网页拿它当滚动），播放器的 keyDown 收不到，表现就是
 * 「敲空格没反应」。带 Alt / Cmd 的菜单快捷键走 main menu，不受影响 ——
 * 这个差别正好可以用来判断是不是焦点问题。
 *
 * IINA 只在窗口重新变成 key window 时把焦点收回去（MainWindowController 的
 * windowDidBecomeKey 里 makeFirstResponder(window)），所以焦点进了 webview
 * 之后，只能靠切走再切回、或者点一下画面来救。
 *
 * 因此这里的原则是：面板不主动弹出来（少一个抢焦点的东西，也不挡画面），
 * 由用户按 Alt+C 或点侧栏标签唤起；另外留一个兜底 —— 焦点确实停在面板里
 * （但不在输入框）时，空格仍然转发给播放器。
 */
function ensurePanel() {
  if (!windowReady) {
    flash("播放器窗口还没就绪，稍等一下", true);
    return;
  }
  if (!panelLoaded) {
    try {
      panel.loadFile("ui/index.html");
      panelLoaded = true;
      bindPanelMessages();
    } catch (e) {
      flash("面板加载失败：" + e, true);
      return;
    }
  }
  try {
    panel.show();
    panelShown = true;
  } catch (e) {
    /* 忽略 */
  }
  syncOut();
}

/* ---------- 网页消息（官方桥） ---------- */

/**
 * 注册面板消息的监听。
 *
 * 关键顺序：`sidebar.loadFile` 内部会调用 `messageHub.clearListeners()`，
 * 把此前注册的所有 onMessage 一次性清掉（见 JavascriptAPISidebarView.loadFile）。
 * 而网页加载是异步的 —— 清监听这一步必然发生在页面脚本执行之前，
 * 所以「先注册监听、再 loadFile」的写法会让页面发出的消息全部石沉大海。
 * 表现是页面明明注入了 window.iina、也发了 ui-ready，插件却一无所知，
 * 极易被误读成「桥没有注入」。
 *
 * 因此：每次 loadFile 之后都要重新注册一遍。
 */
function bindPanelMessages() {
  step("绑定面板消息", function () {
    panel.onMessage("ui-ready", function () {
      gotUiReady = true;
      bridgeUp = true;    // 页面主动报到，说明这一侧确实是通的
      diag({ stage: "ui-ready（官方桥已通）", bridgeUp: bridgeUp });
      pushStateToPanel();
      sayToPanel("marks", marks);
    });

    // 面板下的指令。这个回调来自 WKScriptMessageHandler，跑在主线程，
    // 所以这里可以动播放器。提示仍然只回给面板 —— 面板本来就在用户眼前，
    // 不必为了它去碰画面浮层。
    panel.onMessage("cmd", function (d) {
      const data = (d && d.data) || {};
      switch (d && d.cmd) {
        case "setMark": setMark(data.role, true); break;
        case "clearMarks": clearMarks(true); break;
        case "preview": previewRef(true); break;
        case "toggle": togglePause(); break;
        default: break;
      }
    });
  });
}

/* ---------- 启动 ---------- */

/**
 * 排错用：把本入口实际能拿到什么东西如实记进 @data/diag.json。
 * 插件里很多失败是静默的，没有这一份就无从判断卡在哪一步。
 */
function diag(extra) {
  try {
    const rec = {
      time: new Date().toISOString(),
      iinaKeys: Object.keys(iina || {}),
      typeof: {
        sidebar: typeof iina.sidebar,
        overlay: typeof iina.overlay,
        event: typeof iina.event,
        menu: typeof iina.menu,
        core: typeof iina.core,
      },
    };
    Object.assign(rec, extra || {});
    iina.file.write(iina.utils.resolvePath("@data/diag.json"), JSON.stringify(rec, null, 2));
  } catch (e) {
    /* 连诊断都记录不了时，只能放弃 */
  }
}

diag({ stage: "脚本开始执行" });

/** 每一步单独兜错并记录。沙箱会把脚本异常直接丢弃，不逐段包起来就看不出卡在哪一步。 */
const log = [];
function step(name, fn) {
  try {
    fn();
    log.push({ step: name, ok: true });
  } catch (e) {
    log.push({ step: name, ok: false, error: String((e && e.message) || e) });
  }
  diag({ log: log, gotUiReady: gotUiReady, bridgeUp: bridgeUp });
}

const MENU = [
  ["打开夹芝士面板", function () { ensurePanel(); }, "Alt+c"],
  ["标记 A（面包起点）", function () { setMark("a"); }, "Alt+a"],
  ["标记 B（插入点）", function () { setMark("b"); }, "Alt+b"],
  ["标记 C（面包终点）", function () { setMark("c"); }, "Alt+s"],
  ["清空打点", function () { clearMarks(); }, null],
  ["试听 A→C（旁白就照这段的语气来）", function () { previewRef(false); }, "Alt+p"],
];

// 关键点：播放器窗口就绪之前 sidebar 不可用，过早调用会抛
// "sidebar.loadFile called when window is not available"。
// 必须挂到 iina.window-loaded 之后再动它；菜单项不受此限制，可以直接注册。
step("挂载 iina.window-loaded", function () {
  iina.event.on("iina.window-loaded", function () {
    windowReady = true;
    // 预加载页面，这样点侧栏标签时立刻有内容。但不 show —— 侧栏自己弹出来
    // 既挡画面，又是把一个 WKWebView 摆到能抢走键盘焦点的位置上。
    step("sidebar.loadFile", function () {
      panel.loadFile("ui/index.html");
      panelLoaded = true;
      bindPanelMessages();     // 必须排在 loadFile 之后，否则监听会被它清掉
    });
  });
});

MENU.forEach(function (m) {
  step("菜单：" + m[0], function () {
    iina.menu.addItem(m[2] ? iina.menu.item(m[0], m[1], { keyBinding: m[2] }) : iina.menu.item(m[0], m[1]));
  });
});

step("周期同步", function () {
  setInterval(function () {
    if (bridgeUp) {
      // 桥通了：状态直接推给页面，纯本地调用，一轮网络请求都不发
      pushStateToPanel();
      return;
    }
    // 桥没通才退回后端会话。这里不以「面板显示过」为闸门：点侧栏标签打开面板
    // 走不到菜单那条路，拿不到 panelShown，闸门一关面板就永远是空的
    pushState();
    drainPanelCommands();
  }, 1200);
});

diag({ stage: "脚本执行完毕", log: log, gotUiReady: gotUiReady });
