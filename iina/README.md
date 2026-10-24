# 两面包夹芝士 · IINA 插件（技术文档）

把「补一句它没说过的话」这件事搬进播放器完成。过去需要打开浏览器、上传文件、
在时间轴上找位置；装上这个插件之后，所有操作都在 IINA 窗口内完成。

本文讲**实现**：技术栈、结构、消息协议、接口、数据流与开发期更新方式。
排错、验收清单与实测记录单列在 [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)。

本机实测环境：**IINA 1.5.0（build 180，mpv 0.41.0）**，macOS arm64。

---

## 目录

1. [概览](#概览)
2. [技术栈](#技术栈)
3. [目录结构](#目录结构)
4. [运行时架构](#运行时架构)
5. [面板与插件：消息协议](#面板与插件消息协议)
6. [面板与后端：接口](#面板与后端接口)
7. [面板的内部结构](#面板的内部结构)
8. [一次生成的完整链路](#一次生成的完整链路)
9. [安装与开发期更新](#安装与开发期更新)
10. [设计约定](#设计约定)
11. [已知限制](#已知限制)

---

## 概览

- 主面板是播放器右侧的**侧边栏标签**（标签名「夹芝士」）。它**不会自己弹出来** ——
  用 `Alt+C` 或点标签唤起
- 即时反馈是**画面浮层**（`iina.overlay`），打点与校验提示直接叠在视频上
- 快捷键允许边看边打点，无需离开全屏
- 重活（抽轨、合成、拼接、质检）全部交给本仓库的 Python 后端，插件只做取状态与下指令

```
[A→B 原声] + [合成旁白] + [B→C 原声]
```

三个点位的含义与约束：`A` 采样起点、`B` 插入点、`C` 采样终点，插回去时须满足 `A < B < C`。
只合成旁白时 `B` 可以没有（它那时只影响参考窗口的优选位置）。

### 快捷键

| 快捷键 | 动作 |
|---|---|
| `Alt+C` | 打开夹芝士面板 |
| `Alt+A` | 标记 A（面包起点） |
| `Alt+B` | 标记 B（插入点） |
| `Alt+S` | 标记 C（面包终点） |
| `Alt+P` | 试听 A→C |

> `Alt+C` 让给了「打开面板」，所以「标记 C」绑在 `Alt+S` 上。

---

## 技术栈

面板界面用**原生 HTML + CSS + JavaScript** 写成，一个文件自包含，**没有框架、
没有打包器、没有 npm 依赖、也没有构建步骤** —— `install.sh` 的全部工作就是拷贝文件。

| 层 | 用什么写 | 运行环境 |
|---|---|---|
| 面板界面 | HTML + CSS（CSS 变量 + `prefers-color-scheme` 做深浅色）+ 原生 JS（IIFE，ES5 语法） | IINA 侧边栏的 `WKWebView`，页面以本地文件加载（`sidebar.loadFile`） |
| 波形 | `<canvas>` 手绘（每条轨道一个 canvas，按像素取峰值画竖条） | 同上 |
| 插件逻辑 | JavaScript，**IINA 插件 API**（`iina.core` / `mpv` / `overlay` / `sidebar` / `menu` / `event` / `http` / `file` / `utils`） | IINA 内的 JavaScriptCore，**不是浏览器沙箱**，没有 DOM |
| 面板 ↔ 插件 | 官方消息桥 `window.iina`（底层是 `WKScriptMessageHandler` 与 `evaluateJavaScript`） | 双向即时 |
| 面板 ↔ 后端 | `fetch` + JSON，后端是 FastAPI（Python），只监听 `127.0.0.1:8733` | 回环 HTTP |

选原生方案的理由：插件页面只在侧边栏那个 200 多像素宽的地方出现，交互集中在打点、
微调与发起生成三件事上，引入框架带来的体积与构建流程都不划算；同时插件的 JS 与页面的 JS
跑在两个完全不同的环境里（一个只有 IINA API，一个只有 DOM），共享代码的余地本来也小。

面板用到的浏览器能力刻意保持基础，只有 `fetch`、`canvas`、CSS 变量与
`prefers-color-scheme` 这些；没有 ES Module，也没有 Service Worker ——
页面以本地文件加载（私有源），模块化与预加载策略在这类源上都不可靠。

---

## 目录结构

| 文件 | 行数 | 职责 |
|---|---|---|
| `Info.json` | 配置 | 插件标识、权限、允许域名、侧边栏标签名、默认端口 |
| `global.js` | 10 | **Global 入口**，整个应用一个实例。当前只打一行日志，为「由插件自行拉起后端」预留 |
| `main.js` | 614 | **Main 入口**，每个播放器窗口一个实例：播放状态、菜单与快捷键、面板加载、浮层、消息桥 |
| `ui/index.html` | 861 | 侧边栏面板本体：样式、结构、脚本全部自包含 |

`install.sh` 只拷贝这四项（`Info.json` / `global.js` / `main.js` / `ui/`），
仓库里的文档不会进插件目录。

---

## 运行时架构

```
┌───────────── IINA ─────────────┐
│  main.js（Main 入口，每窗口一份）│──── iina.core / mpv / overlay / menu
│        ▲ 消息桥（双向即时）      │
│  ui/index.html（侧边栏 webview） │
└───────────────┬─────────────────┘
                │ fetch（只走重活与波形）
        ┌───────▼────────┐
        │ 后端 FastAPI    │── 抽轨 / 合成 / 拼接 / 质检 / 波形
        └────────────────┘
```

三层各自的边界很硬：

- **插件**能读播放器、能控制播放器、能弹浮层；拿不到 DOM，也不该做重活
- **面板**能画界面、能收输入；**拿不到播放器**（没有 `iina.core`），要动播放器必须下指令
- **后端**只做计算与文件；它对「播放器当前在播什么」一无所知，除非插件上报

### 状态与推送节奏

- 主循环 **1200 ms** 一轮：试听的到点停止（`watchPreview`）挂在这里，同时把状态推给面板
- 播放期间额外以 **110 ms** 推送，让面板上的播放头走得连贯 —— 这只在消息桥上开启。
  桥上一次推送是一次本地 `evaluateJavaScript`；退回后端会话时每一轮都是 HTTP 请求，
  那样刷等于每秒十次网络请求
- 暂停且没在播区间时，高频推送一条都不发

---

## 面板与插件：消息协议

IINA 在文档创建时就把 `window.iina` 注入插件页面（`postMessage` 走
`webkit.messageHandlers.iina`，`onMessage` 收插件推来的消息），插件侧对应
`iina.sidebar.postMessage` / `onMessage`。这是官方推荐的通道，IINA 自带的
OpenSubtitles 插件（`io.iina.opensub`）用的就是它。

```
面板 → 插件   window.iina.postMessage("cmd", { cmd, data })
面板 → 插件   window.iina.postMessage("ui-ready", {})      // 页面起来时报到一次
插件 → 面板   panel.postMessage("state" | "marks" | "notice", …)
```

### `cmd`（面板下给插件的指令）

| cmd | data | 作用 |
|---|---|---|
| `setMark` | `{role}` | 把当前播放位置打成该点 |
| `setMarkAt` | `{role, t}` | 以指定时间打点（拖动波形微调后落库走这条） |
| `clearMarks` | — | 清空三个点，并取消正在播的区间 |
| `preview` | — | 播 A→C；再发一次是停止（toggle） |
| `playRange` | `{from, to, label}` | 播任意区间（轨道上那一小段用这条） |
| `stop` | — | 停止区间播放 |
| `goto` | `{t}` | 跳到指定时间 |
| `toggle` | — | 暂停 / 继续（焦点停在面板里时替播放器按一下空格） |

### `state`（插件推给面板）

| 字段 | 含义 |
|---|---|
| `path` / `name` | 当前文件的本机路径与显示名 |
| `pos` / `dur` | 播放位置与总时长。位置取自 mpv 的 `time-pos`（双精度），打点才能落在毫秒上 |
| `marks` | `{a, b, c}`，未打点为 `null` |
| `playing` | 是否正在播某段区间 |
| `prange` | 正在播的区间 `{from, to}`；面板靠它画播放头、判断 A→C 条要不要点亮 |

`notice` 是一句要显示的话：`{text, is_error, at}`，面板只显示 6 秒内的。
它存在的唯一原因是：`iina.http` 的回调跑在后台队列，在那里面碰播放器 UI 会让 IINA
直接 abort，所以从 http 回调发起的动作只能把话交给面板说。

### 桥不可用时的降级（两套协议并存）

两套协议并存，面板启动时按页面里有没有 `window.iina` 自动选择。没有桥时，
`cmd` 会被翻译成后端会话字段（`send()` 里的 `LEGACY` 表）：

| cmd | 退化成 | 代价 |
|---|---|---|
| `setMark` / `setMarkAt` / `clearMarks` | 写 `POST /api/session` 的 `marks` | 无 |
| `goto` | 写 `want_goto` | 无 |
| `playRange` | 退成跳到区间开头 | **到点不会自动停**，交给用户按空格 |
| `stop` / `toggle` | 写 `want_pause_toggle` | 无 |

另有两道保险：面板的**看门狗**（桥在但 2.5 秒收不到任何状态就降级轮询，状态栏写明原因），
以及插件侧对每条指令的**最小间隔**（`CMD_GAP_MS`）—— 清零指令的那次 POST 若丢了，
同一条指令会在下一轮又被读成「还没执行」，变成每轮跳一次、播一次的循环。

这套降级存在的意义是：**桥一旦出问题，面板应当只是变慢，而不是变瞎。**

### 桥的两个使用细节

- **`onMessage` 必须注册在 `loadFile` 之后。** `sidebar.loadFile` 内部最后一步是
  `messageHub.clearListeners()`，会把此前注册的全部监听清掉；而网页加载是异步的，
  清监听必然发生在页面脚本执行之前。写成「先注册、再 loadFile」，页面发出来的消息
  会全部石沉大海。所以 `main.js` 里是 `bindPanelMessages()`，每次 `loadFile` 之后重新注册
- `window.iina.onMessage(name, cb)` 是**覆盖式**的，同名只留最后一个回调。
  一个消息名只用一个回调，别在两处注册同一个名字

### 打点以插件本地为准

桥通时，面板按钮与快捷键两条来源都直接落到插件本地的 `marks`，由插件单向推给面板，
**不存在两份真相**。桥不通时才需要合并：面板写进会话，快捷键写在插件本地，
插件每轮以会话为准补齐（`main.js` 的 `syncMarks`）。

---

## 面板与后端：接口

| 接口 | 用途 |
|---|---|
| `GET /api/session` | 桥不通时面板靠它轮询取状态；桥通时只用于探测后端是否活着 |
| `POST /api/session` | 写会话；`marks` 按字段合并，两个来源各写一半也不会互相覆盖 |
| `POST /api/open` | 按本机路径导入；**复制而非移动**，播放器里的原文件不受影响 |
| `POST /api/job/<id>/generate` | 发起生成；body 含 `a/b/c`、`text`、`mode`、`speed`、`qc` 等 |
| `GET /api/job/<id>` | 轮询进度（`stage` / `progress` / `message` / `result`） |
| `GET /api/peaks?path=&center=&span=&res=` | 取打点附近一小段波形，供三条微调轨道用 |
| `POST /api/reveal` | 在访达中显示产物；出于安全只接受输出目录内的路径 |
| `GET /media/<job_id>/…` | 产物音频；实现了 Range 206，浏览器才能拖进度条 |

后端在 `server/main.py` 挂了 `CORSMiddleware`：面板页面的加载源与 `127.0.0.1:8733` 不同源，
带 JSON `Content-Type` 的 POST 会先发 `OPTIONS` 预检，没有中间件时预检落到路由上就是
`405`，真正的 POST 根本不会发出。

`mode` 三种取值：`sandwich`（A→B + 旁白 + B→C）、`full`（0→B + 旁白 + B→末尾）、
`narration`（**只合成旁白，不切不拼**）。面板上对应两个按钮，Web 端对应三个单选项。

---

## 面板的内部结构

`ui/index.html` 的脚本部分是一个 IIFE，结构很直白：**一个状态对象 + 一个渲染函数**。

```
st          所有状态：位置、时长、打点、后端连通性、任务、结果、区间播放状态
paint()     唯一的渲染出口，幂等地按 st 重绘整个面板
send(cmd)   面板 → 插件的唯一出口（桥或降级二选一）
applyState() 插件推来的状态落进 st；拖动期间跳过 marks，避免被旧值打回去
```

没有虚拟 DOM，也没有局部更新：状态一变就整面板重绘。面板就那么大，这个开销可以忽略，
换来的是「界面永远等于状态」这一条简单性质。

几个主要部件：

| 部件 | 说明 |
|---|---|
| 打点格 `mA/mB/mC` | 显示毫秒级时间（`fmtMs`），格内按钮直接打点 |
| A→C 试听条 `refbar` | A 与 C 打齐才出现；播放头按 A→C 归一化；B 用芝士色标在条上；点任意处 `goto` |
| 微调轨道 `lcA/lcB/lcC` | 三个 canvas，各以自己的打点为中心画同一段波形；中间那条**固定的竖线**才是打点位置，右拖 = 打点前移 |
| 播放头 | 随播移动的竖线（带三角，与固定的打点竖线区分）；扫出视野时边缘给方向箭头 |
| 波形宽度 | 轨道的可视半窗口 ±0.25s ~ ±8s；越窄越精细，也决定轨道 ▶ 播多长 |
| 生成按钮 | 「只合成旁白」要 A、C 即可；「合成并插回去」要 A<B<C |

波形数据单独一路：`ensurePeaks(role)` 向 `/api/peaks` 要「打点附近一小段」的峰值，
格宽固定毫秒级（整轨压成几千格的做法放大到秒级以下就糊了）。
本地有 `PEAKS` 缓存并留两倍窗口的余量，拖动时通常连请求都不用发。

拖动的处理值得一提：`dragging` 置位期间，`applyState` 会**跳过**插件推来的 `marks`。
插件那边的打点要到松手才更新，它每轮推的还是旧值，照单全收的话拖到一半会被打回去。

---

## 一次生成的完整链路

```
点「合成并插回去」
  面板 POST /api/open {path}              后端复制文件、抽音轨 → job_id
  面板 POST /api/job/<id>/generate {a,b,c,text,mode,speed,…}
  面板 GET  /api/job/<id>  每 700ms 轮询 stage
  后端  ref → synth →（插回模式：slice → loud → concat）→ export → qc
  面板 收到 stage=done：显示产物路径，给出「在访达中显示」
```

面板与后端之间只有这几步；插件完全不参与生成，也不感知进度。
生成期间面板把两个按钮都禁用，进度文案直接来自后端的 `message`。

---

## 安装与开发期更新

**前置**：插件不含模型，先按仓库根目录 `README.md` 完成后端部署，确认
`curl -s http://127.0.0.1:8733/api/health` 的 `missing` 为空数组。

```bash
./iina/install.sh          # 拷贝到 ~/Library/Application Support/com.colliderli.iina/plugins/
defaults write com.colliderli.iina "PluginEnabled.local.bread-cheese-bread" 1
```

IINA 在启动时扫描插件目录，装完或改完都要**完全退出再启动**（`Cmd+Q`，仅关窗口无效）：

```bash
pkill -x IINA && open -a /Applications/IINA.app <视频文件>
```

开发期的更新方式：

| 改了什么 | 怎么生效 |
|---|---|
| `iina/` 下任何文件 | `./iina/install.sh` + 完全退出 IINA 再打开 |
| `server/` 下任何文件 | 重启 `./run.sh` |

改插件逻辑又不想反复重启 IINA 时，用**离线仿真**：mock 掉 IINA 的全部 API，
用 node 的 `vm` 跑 `main.js`，把 `setInterval` 换成手动触发；面板脚本同理，
用最小 DOM mock + 假 `fetch`。这比实机快得多，改逻辑先跑它。
仿真要连 IINA 的语义一起 mock（尤其是 `clearListeners`），并做「去掉修复」的对照组，
否则验不出时序类问题。

---

## 设计约定

改代码前值得知道的几条，都是踩过之后定下来的：

1. **插件面板不重复播放器已有的控件。** 整片进度条已经删掉（播放器自己有），
   只保留「文件名 + 时间码」这类插件自己的上下文，以及 A→C 这种播放器表达不出来的区间
2. **面板不主动 `show()`。** 侧栏是个 `WKWebView`，弹出来既挡画面，又是一个能抢走键盘
   焦点的东西。`window-loaded` 时只 `loadFile` 预加载，由 `Alt+C` 或点标签唤起
3. **`iina.http` 的回调里绝不碰播放器 UI。** 那个回调在 `NSURLSession-delegate` 后台队列，
   碰 `overlay` / `osd` / `sidebar` 会让 IINA 直接 abort。IINA 没有派发回主线程的方法，
   所以这类反馈一律走会话的 `notice` 字段，由面板渲染
4. **`onMessage` 排在 `loadFile` 之后**（见上文），每次 `loadFile` 后重新注册
5. **打点的精度要两头都改**：读值改 mpv `time-pos`（双精度），显示改毫秒格式。
   只改显示会以为修好了
6. **旁白去匹配面包的响度**，不把成品整体压 loudnorm；原始片段一个采样点都不动
7. **指令要有最小间隔**，见上文降级一节

## 已知限制

- **后端需手动启动**。尚未实现由插件自行拉起并在退出时回收；`global.js` 即为该功能预留
- **成品需手动取用**。面板给出真实路径与「在访达中显示」，尚未实现一键回到播放器播放成果
- **素材限定本地文件**。`POST /api/open` 按路径复制，网络流地址不适用
- **键盘焦点会被侧栏网页接走**。侧栏拿到 first responder 之后，空格这类不带修饰键的按键
  落在网页里，播放器的 `keyDown` 收不到；`Alt` / `Cmd` 一类的菜单快捷键走 main menu，
  不受影响 —— 这个差别可以用来判断是不是焦点问题。救回来的办法是切走再切回、点一下画面，
  或在面板里按 `Esc`；面板同时留了兜底：焦点停在面板里（但不在输入框）时空格转发给播放器
- **改动需完全重启**。插件与后端都一样，仅关窗口无效
- **插件标识为开发用值** `local.bread-cheese-bread`。对外发布前建议改为反向 DNS 形式
- **手动重载插件可能崩溃**（SIGTRAP）。栈是 `reloadAllPlugins → iina.window-loaded →
  sidebar.loadFile`，大概率是 IINA 侧解包问题，JS 层捕获不到。正常启动不受影响

---

## 相关文档

- [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) —— 诊断入口、排错表、五个致命坑、验收清单、实测记录
- [`../docs/iina-probe.md`](../docs/iina-probe.md) —— 早期探针对 IINA API 的实测记录
- [`../README.md`](../README.md) —— 项目本体：部署、参数、语速、质检
