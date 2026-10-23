# IINA 插件可行性探针 · 实测结论

在 IINA **1.5.0（build 180，mpv 0.41.0）** 上用真实探针跑出来的结果。所有条目都附带当时的判定依据，
未验证的一律标为「未测」，不含推测。探针脚本本身在 `iina/global.js`、`iina/main.js`、`iina/probe.html`。

> **2026-10-23 更正**：下面「唯一没打通的是网页 → 插件的消息桥」这一条站不住。
> 当时真正测的是 **standaloneWindow**；侧边栏的 `window.iina` 从未被测过 ——
> 我们的面板页面里一次 `iina.postMessage` 都没发过，插件侧的 `onMessage` 自然收不到。
> 官方自带的 OpenSubtitles 插件就是靠侧边栏这个桥工作的。
> 现在的主通道已改成官方桥，本文件保留作为当时的探测记录。

## 已确认可用

### 插件脚本侧

| 能力 | 结论 | 依据 |
|---|---|---|
| `http.get` 访问本机回环 | 可用 | `probe-global.json` 里 127.0.0.1、localhost 均返回后端 JSON |
| 域名白名单是否拦本地请求 | 拦外网、放行本地 | 未登记的 `https://example.com` 报 `URL https://example.com is not allowed.`，本地地址不受此限 |
| `utils.exec` 调外部二进制 | 可用 | 绝对路径调 Python 3.11.17、ffmpeg 9.0.1 均 status 0 |
| 后台进程能否在 exec 返回后存活 | 能 | 派生的 `sleep 5; echo alive > marker` 五秒后成功落盘，标记内容为 `alive` |
| 文件读写 | `@data` 稳定可用 | 解析为 `<IINA 支持目录>/plugins/.data/<插件标识>/`；写 `/tmp` 时成功时失败，不应依赖 |
| 菜单项、OSD | 可用 | 菜单项成功注册；`core.osd` 调用无异常 |
| 播放状态读取 | 可用（仅 Main 入口） | `core.status` 返回 url / title / position / duration / paused / idle / isNetworkResource |
| mpv 属性 | 可用（仅 Main 入口） | `mpv.getNumber("time-pos")` 返回数值 |

### 环境细节

- 插件进程的 `PATH` 为 `/usr/gnu/bin:/usr/local/bin:/bin:/usr/bin:.`，**不含 `/opt/homebrew/bin`**，
  所以 `fileInPath("ffmpeg")` 返回 false。解决办法：用绝对路径，或用 `/bin/bash -lc` 走登录 shell，
  后者的 PATH 含 `/opt/homebrew/bin`，`command -v ffmpeg` 能定位到。
- `iina.core` 在 Global 入口取不到（返回「core 在此入口不可用」），播放相关逻辑必须放 Main 入口。
- `iina.http`、`iina.utils`、`iina.menu`、`iina.standaloneWindow` 两个入口都能用。

### 网页侧（standaloneWindow）

- 用 `loadFile("probe.html")` 打开的页面 **能直接用 fetch 和 XHR 访问 `http://127.0.0.1:8733`**，
  后端访问日志里两条带标记的请求都是 200。现有 `web/` 里的界面逻辑基本可以原样搬过来。

## 已确认不可行 / 注意事项

1. **「网页 → 插件」的消息桥不通。** 无论 Global 还是 Main 入口，`iina.postMessage` 发出后插件侧
   `onMessage` 都没有收到。轮询等待注入完成后再发也一样。
2. **`loadFile` 的参数不能带查询串。** `loadFile("probe.html?file=...")` 会导致页面完全不加载
   （后端再没收到任何来自页面的请求），而 `loadFile("probe.html")` 正常。选型时不能靠 URL 传参。
3. IINA 注入给网页的脚本至少有两类，且都不是给独立窗口用的：
   偏好页注入 `window.iina.preferences`（get/set + `_post`），
   另一份带 `listeners` / `onMessage` / `postMessage` / `_simpleModeSetContent` 的对应的是 Overlay 的简单模式。
   这解释了第 1 条。

## 由此确定的架构

既然「插件 → 本机 HTTP」「网页 → 本机 HTTP」两条路都通，**后端天然就是两者的中介**，
不必依赖 IINA 的消息桥：

```
IINA 播放器                IINA 插件脚本              本机后端（既有 FastAPI）
  当前文件/播放位置  ──▶  Main 入口读 core.status ──▶  POST /api/session
                          │
                          └─▶ standaloneWindow.loadFile("ui/index.html")   ← 裸路径，不带查询串
                                       │
                                       └─▶ 页面用 fetch 直连后端：取 session、跑流水线、轮询进度
```

分工：

- **Main 入口**：读当前媒体与播放位置，写进后端 session；注册菜单项（标记 A/B/C、打开制作窗口）；
  OSD 反馈；结果出片后在 Finder 中显示。
- **Global 入口**：负责后端进程（必要时拉起），无播放相关逻辑。
- **网页界面**：复用现有 `web/` 的交互逻辑，改为从后端读取 session，不再走上传文件的路子。
- **后端**：新增一个「按路径导入」的能力（现在只有上传），以及 session 的读写。

这条路用到的能力全部已实测通过，没有依赖任何未验证的机制。

## 待办

- [ ] 后端新增 `POST /api/session`（存当前媒体路径与 A/B/C）与 `GET /api/session`
- [ ] 后端新增按本机路径导入的接口（现在只能上传文件流）
- [ ] 把 `web/` 改成从 session 初始化，去掉上传环节
- [ ] Main 入口注册菜单项：标记 A / 标记 B / 标记 C / 打开制作窗口
- [ ] 明确是否要「插件自行拉起后端」；技术上可行（后台进程可存活），但要考虑退出时的回收
- [ ] 插件标识 `local.bread-cheese-bread` 若要正式发布，建议换成自有域名的反向 DNS
