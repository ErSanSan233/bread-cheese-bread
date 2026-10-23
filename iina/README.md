# 两面包夹芝士 · IINA 插件

把「补一句它没说过的话」这件事搬进播放器完成。过去需要打开浏览器、上传文件、
在时间轴上找位置；装上这个插件之后，所有操作都在 IINA 窗口内完成。

- 面板位于播放器右侧的**侧边栏标签**（标签名「夹芝士」）。它**不会自己弹出来** ——
  用 `Alt+C` 或点标签唤起。这样既不挡画面，也避免键盘焦点被侧栏网页接走
  （见[已知限制](#已知限制)里的焦点一条）
- 即时反馈是**画面浮层**，打点与校验提示直接叠在视频上
- 快捷键允许边看边打点，无需离开全屏

本机实测环境：**IINA 1.5.0（build 180，mpv 0.41.0）**，macOS arm64。

---

## 目录

1. [它做了什么](#它做了什么)
2. [安装](#安装)
3. [使用流程](#使用流程)
4. [运行机制](#运行机制)
5. [测试与验收](#测试与验收)
6. [实测记录](#实测记录)
7. [已知限制](#已知限制)
8. [排错](#排错)

---

## 它做了什么

### 核心操作

素材导入后，指定三个时间点，程序学习与其中一段说话人的音色和语气，
用这段语气说出新写的旁白，并把旁白插回指定位置。

| 点位 | 含义 |
|---|---|
| **A** | 采样区间起点（面包起点） |
| **B** | 旁白的插入位置（芝士的位置） |
| **C** | 采样区间终点（面包终点） |

三者必须满足 `A < B < C`。产出为：

```
[A→B 原声] + [合成旁白] + [B→C 原声]
```

### 插件提供的界面

| 元素 | 位置 | 作用 |
|---|---|---|
| 文件名与实时时间码 | 侧边栏顶部 | 显示当前播放文件与进度 |
| A→C 试听条 | 侧边栏 | A 与 C 都打好后出现；试听时播放头在里面走，B 的位置标在条上，点任意一处可跳过去 |
| A / B / C 打点格 | 侧边栏 | 显示已打点时间（**精确到毫秒**），格内按钮可直接打点 |
| 三条微调轨道 | 侧边栏 | 同一段波形画三遍，各以自己的打点为中心；左右拖波形即挪动该点 |
| 轨道上的 ▶ | 侧边栏 | 播放**这一条轴看得见的那一小段**（±波形宽度），到点自动停；再点一次停 |
| 播放头 | 轨道内 | 正在播的位置，随播放实时移动；落在视野外时轨道边缘给一个方向箭头 |
| 波形宽度 | 侧边栏 | 轨道的可视半窗口，±0.25s ~ ±8s，越窄越精细，也决定 ▶ 播多长 |
| 试听 | 侧边栏 / `Alt+P` | 跳回 A 点播放 A→C，**到 C 自动停**；再点一次可手动打断 |
| 旁白输入框 | 侧边栏 | 待合成的文本 |
| 只合成旁白 | 侧边栏 | 只用 A→C 的音色念一遍，单独导出，**不碰原声**（A、C 打齐即可，B 可以没有） |
| 合成并插回去 | 侧边栏 | 切成 A→B + 旁白 + B→C 的完整成品（需要 A<B<C） |
| 连接状态灯 | 侧边栏底部 | 绿点为已连上本机后端 |
| 画面提示 | 视频浮层 | 打点结果、越界校验失败等原因 |

### 快捷键

| 快捷键 | 动作 |
|---|---|
| `Alt+C` | 打开夹芝士面板 |
| `Alt+A` | 标记 A（面包起点） |
| `Alt+B` | 标记 B（插入点） |
| `Alt+S` | 标记 C（面包终点） |
| `Alt+P` | 试听 A→C |

> 注意：`Alt+C` 用于打开面板，因此「标记 C」改绑到 `Alt+S`。

---

## 安装

### 前置条件

插件本身不含模型，合成与质检全部由本仓库的 Python 后端承担。
**先按仓库根目录 `README.md` 完成部署**，并确认 `./run.sh` 能正常启动，
健康自检的五项均为通过：

```bash
./run.sh
curl -s http://127.0.0.1:8733/api/health
# {"ok":true,"ffmpeg":true,"tts_python":true,"tts_ckpt":true,
#  "whisper":true,"whisper_model":true,"missing":[]}
```

### 安装插件

```bash
./iina/install.sh
```

该脚本把下列文件拷到 `<IINA 支持目录>/plugins/local.bread-cheese-bread.iinaplugin/`：

| 文件 | 行数 | 职责 |
|---|---|---|
| `Info.json` | 配置 | 权限、允许域名、侧边栏标签名、默认端口 |
| `main.js` | ~310 | **Main 入口**，每个播放器窗口一个实例，负责播放状态、菜单、面板、浮层 |
| `global.js` | ~10 | **Global 入口**，全局唯一实例，当前只作占位 |
| `ui/index.html` | ~310 | 侧边栏面板本体（自包含，含样式与脚本） |

### 首次启用并重启

IINA 扫描到插件后仍需要打开开关，且必须**完全退出再启动**（`Cmd+Q`，仅关闭窗口无效）：

```bash
defaults write com.colliderli.iina "PluginEnabled.local.bread-cheese-bread" 1
pkill -x IINA
open -a /Applications/IINA.app <视频文件>
```

---

## 使用流程

1. 在仓库根目录执行 `./run.sh`，保持后端运行
2. 用 IINA 打开一个**本地**视频或音频文件
3. 打开右侧「夹芝士」标签（`Alt+C` 可直接定位）
4. 播放到取样区间起点时按 `Alt+A`，终点按 `Alt+S`
5. 播放到需要补话的位置按 `Alt+B`
6. **微调**：三条轨道里左右拖波形，把每个点挪到准确的位置（详见[微调打点](#微调打点)）
7. 建议先按「试听」，确认参考语音的语气符合预期
7. 在输入框写下旁白
8. 想先听一句效果就点「**只合成旁白**」—— 它不切原声，只按 A→C 的音色念一遍并单独导出
9. 满意了再点「**合成并插回去**」，这才是完整的两面包夹芝士
10. 首次生成需加载模型，约 1~2 分钟；之后每次约 20~40 秒。
    想改语速就拖面板上的「语速」滑条（往右更快），改完重新合成一次即可
11. 完成后面板会显示产物的**路径**，点「在访达中显示」可直接在访达里选中它

### 微调打点

快捷键打下来的点往往差几十毫秒，正好卡在字中间。三条轨道就是为这个准备的：

- **一条轨道一个点**，同一段波形画三遍，各自以自己的打点为中心
- **中间那条固定的竖线才是打点的位置**。把波形往右拖，等于让竖线指向更早的内容 ——
  也就是把打点往前挪。反过来往左拖则往后挪
- 拖到边界会自动停住，A 不会越过 B，B 始终夹在 A 与 C 之间，C 不会超出片尾
- **松手才落库**，并让播放器跳到该处，方便听一下这个点落在哪个字前面。
  拖动途中不打扰播放器
- 「波形宽度」决定放大倍率：默认 ±1.00s，在这个宽度下拖 1 像素约等于 8 毫秒；
  要更精细就调窄，要找大致位置就调宽

### 为什么不再画整片的进度条

播放器自己就有进度条，面板里再画一条是重复。真正需要在面板里单独呈现的，
只有旁白要照着来的那一段 —— 所以 A 与 C 都打好后，打点格下方会出现一条
**A→C 试听条**：

- 条的两端就是 A 与 C（带毫秒），中间写着这一段的长度，以及 B 落在进入后第几秒
- 试听时播放头在里面从左到右走，正在播 A→C 时这条边框会点亮
- 条上标出 B 的位置（芝士色），一眼看出旁白会插在哪儿
- 点条上任意一处就跳过去 —— 想从中间听一句，不必先去拖播放器
- 播放头只在这段之内才画：播到 A→C 之外时它对试听没有意义

### 轨道上的 ▶ 与播放头

耳朵比眼睛更能判断「这个点是不是卡在字中间」，所以每条轴都能自己播：

- ▶ 播的就是**这条轴看得见的那一小段**（打点 ±波形宽度），播完自动停
- 播放头是一条随播移动的竖线，带三角，跟中间那条固定的打点竖线区分开；
  暂停时也画，方便直接看当前位置落在哪儿
- 播 A→C 那种长区间时播放头多半已经扫出视野，这时轨道边缘会给出方向箭头 ——
  想让它留在视野里，把波形宽度调宽到 ±4s / ±8s 即可
- **可以边听边挪**：某条轴正在播的时候拖动它，松手会接着从新位置播，不用再点一次

为此插件会在播放期间把位置推得更勤（110 毫秒一次）。这只开在官方桥上 ——
桥上的一次推送是一次本地 `evaluateJavaScript`，而退回后端会话时每一轮都是
一次 HTTP 请求，那么刷就等于每秒十次网络请求。暂停且没在播区间时一条都不推。

波形不是整轨压成的粗粒度概览 —— 那样放大到秒级以下就糊了。面板只向后端要
「打点附近一小段」的波形（`GET /api/peaks`），格宽固定毫秒级，
所以两小时的片子也一样快（实测 24 秒窗口约 0.24 秒返回，同一段会命中缓存）。
拖出已取范围时会再取一次。

---

## 运行机制

### 三层结构

| 层 | 运行位置 | 职责 |
|---|---|---|
| 面板 | IINA 侧边栏 webview | 展示状态、接收输入 |
| 插件脚本 | IINA 的 Main 入口（JavaScriptCore） | 读播放状态、控制播放器、显示浮层 |
| 后端 | 仓库根目录 `./run.sh` | 抽轨、合成、拼接、质检 |

### 面板与插件怎么通信：官方桥为主，后端会话兜底

IINA 在文档创建时就把 `window.iina` 注入进插件页面（`postMessage` 走
`webkit.messageHandlers.iina`，`onMessage` 收插件推来的消息），插件侧对应的就是
`iina.sidebar.postMessage` / `onMessage`。**这是官方推荐的通道，即时双向，无需轮询。**
IINA 自带的 OpenSubtitles 插件（`io.iina.opensub`）用的就是它。

> 更正一处旧结论：这里原先写着「本机 1.5.0 上侧边栏与浮层的桥均未注入可用实例」。
> 那是错的 —— 当年真正测过的是 **standaloneWindow**，侧边栏的桥从未测过；
> 而我们的面板页面里一次 `window.iina.postMessage` 都没调用过，
> 插件侧那几个 `onMessage` 自然永远等不到，「ui-ready 收不到」被误读成了「桥不存在」。
> 阶段一的探针记录仍保留在 [`docs/iina-probe.md`](../docs/iina-probe.md)。
>
> 换成桥之后第一次仍然收不到，这次的原因换了一个：`loadFile` 会清监听
> （见下方「桥的三个使用细节」）。**同一个症状背后可以是两个完全不同的原因**，
> 所以判断「桥通不通」不能只看有没有收到消息。

现在的通道：

```
桥（主）  面板 → 插件   window.iina.postMessage("cmd", {cmd, data})
桥（主）  插件 → 面板   panel.postMessage("state" / "marks" / "notice", …)
后端      面板 → 后端   POST /api/open → /api/job/<id>/generate → 轮询进度
```

`cmd` 目前有 `setMark` / `setMarkAt` / `clearMarks` / `preview` / `playRange` /
`stop` / `goto` / `toggle` 八种；`state` 除文件名、时长、位置、打点之外，
还带 `playing` 与 `prange`（正在播的区间），面板靠这两个画播放头并把 ▶ 切成停止键。

桥通了之后插件不再往后端写状态、也不再取指令 —— 除非后端在跑重活，
否则整条链路上没有周期性的网络请求。面板只在启动和断线时探一次后端是否可达
（后端没起来时每 3 秒重试一次）。

桥不可用时（比如将来某个 IINA 版本不再注入）自动退回旧办法：
面板写 `POST /api/session` 的 `marks` / `want_preview` 等字段，插件轮询取走。
两套协议都还在，靠页面启动时有没有 `window.iina` 自动选择。
另外面板还有一道看门狗：桥在但 2.5 秒内收不到任何状态，就自动降级成轮询，
避免面板白屏等人来救。

### 桥的三个使用细节

- **`onMessage` 必须注册在 `loadFile` 之后。** `sidebar.loadFile` 内部最后一步
  是 `messageHub.clearListeners()`，会把此前注册的所有监听一次性清掉；
  而网页加载是异步的，清监听必然发生在页面脚本执行之前。
  写成「先注册、再 loadFile」，页面发出来的消息会全部石沉大海 ——
  症状是 `diag.json` 里 `loadFile` 成功、`gotUiReady` 却是 false，面板一片空白。
  每次 `loadFile` 之后都要重新注册（见 `main.js` 的 `bindPanelMessages`）
- `window.iina.onMessage(name, cb)` 是**覆盖式**的：同名只留最后一个回调。
  所以一个消息名只用一个回调，别在两处注册同一个名字
- 插件推消息走主线程（`DispatchQueue.main.async` 里的 `evaluateJavaScript`），
  面板发消息走 `WKScriptMessageHandler`（同样主线程）。
  **这与 `iina.http` 的回调不同** —— 后者跑在 NSURLSession 的后台队列，
  在那里面碰播放器 UI 会让 IINA 直接 abort

### 后端为插件新增的接口

| 接口 | 用途 |
|---|---|
| `GET /api/session` | 取播放器会话。桥不通时面板靠它轮询取状态；桥通时只用于探测后端是否活着 |
| `POST /api/session` | 写播放器会话；`marks` 按字段合并，快捷键与面板按钮各写一半也不会互相覆盖。桥通之后不再是主通道 |
| `POST /api/open` | 按本机路径导入；**复制而非移动**，播放器里的原文件不受影响 |

### 打点以插件本地为准

打点有两个来源：面板按钮与快捷键。桥通时两者都直接落到插件本地的 `marks`，
由插件单向推给面板，**不存在两份真相**。

桥不通时才需要合并：面板写进会话，快捷键写在插件本地，
插件每轮轮询以会话为准补齐（`main.js` 的 `syncMarks`）。
缺这一步的话，面板点了 A、再按快捷键打 C，插件本地就只有一半，
面板发起的「试听」会误报「先打上 A 和 C」。

### 开发期改动后的更新方式

修改 `iina/` 下的文件后，重新执行 `./iina/install.sh`，
再完全退出并重启 IINA。后端侧（`server/`）的改动则需要重启 `./run.sh`。

### 重启后端会让整机卡 1~2 分钟（不是故障）

每次 `./run.sh` 都会重新拉起模型进程并加载约 10 GB 权重。加载期间磁盘与内存
被占满，**连同 IINA 在内的其他程序会短暂失去响应**，属正常现象，等终端出现
`[tts_server] 模型就绪` 再操作即可。因此：

- 别连续重启。上一轮还没加载完就再开一个，两个模型进程会叠在一起。
- 强杀后端（连按 Ctrl-C、`kill -9`）会让模型进程变孤儿，继续占着内存。
  下一次启动时 `synth.reap_stale()` 会自动清掉这一类残留，终端会打印
  `清掉 N 个上一轮残留的模型进程`。
- 终端刷 `python(xxxxx) MallocStackLogging: can't turn off ...` 是
  macOS libmalloc 的消息，由模型进程的子进程打印，无害；新版本已过滤掉触发它的
  环境变量。翻历史看 `work/tts_server.log`，每个 `===== worker 启动 =====`
  分隔符之间是一轮。

---

## 测试与验收

下列条目在装好插件、后端运行、播放器打开本地视频的前提下执行。
每条都给出判定依据，便于逐项复核。

### T1 · 后端自检

```bash
curl -s http://127.0.0.1:8733/api/health
```

**通过**：各项均为 `true`，`missing` 为空数组。

### T2 · 插件是否已被 IINA 扫描到

```bash
defaults read com.colliderli.iina PluginOrder
```

**通过**：输出中出现 `local.bread-cheese-bread`。

### T3 · Main 入口每一步的执行情况

```bash
cat ~/Library/Application\ Support/com.colliderli.iina/plugins/.data/local.bread-cheese-bread/diag.json
```

**通过**：`log` 数组中每一项的 `ok` 均为 `true`，
且包含「sidebar.loadFile」「sidebar.show」两项。任一 `ok: false` 时，
同一项的 `error` 字段即为失败原因。

### T4 · 面板是否真的被加载执行

观察运行 `./run.sh` 的那个终端（后端每条请求都会打印一行）：

```
INFO:     127.0.0.1:xxxxx - "GET /api/session HTTP/1.1" 200 OK
```

**通过**：该行持续出现，约每 0.4 秒一行（约每秒 2~3 行），
说明面板的轮询已经启动。此判定不依赖消息桥 —— 页面只要执行了就会留下这些记录。
终端里完全不出现时，按[T3](#t3--main-入口每一步的执行情况)检查面板是否加载成功。

### T5 · 播放状态是否正确上报

```bash
curl -s http://127.0.0.1:8733/api/session
```

**通过**：`alive` 为 `true`，`name` 为当前文件名，
`dur` 为视频总秒数且大于 0。

### T6 · 播放位置是否随时间推进

```bash
curl -s http://127.0.0.1:8733/api/session | python3 -c "import sys,json;print(json.load(sys.stdin)['pos'])"
sleep 5
curl -s http://127.0.0.1:8733/api/session | python3 -c "import sys,json;print(json.load(sys.stdin)['pos'])"
```

**通过**：两次数值不同，差值约等于间隔秒数。

### T7 · 打点能否写入

在播放中用 `Alt+A` / `Alt+B` / `Alt+S` 各打一次点，或点击面板格内的「打点」按钮。
不便操作界面时，可用与面板按钮等价的请求写入：

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"marks":{"a":10,"b":25,"c":60}}' http://127.0.0.1:8733/api/session

curl -s http://127.0.0.1:8733/api/session | python3 -c "import sys,json;print(json.load(sys.stdin)['marks'])"
```

**通过**：返回 `{"a":…, "b":…, "c":…}`，三个均有数值且 `a < b < c`。

面板按钮走的是浏览器，会先发跨源预检，需一并确认预检本身放行：

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X OPTIONS http://127.0.0.1:8733/api/session \
  -H "Origin: null" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type"
```

**通过**：输出 `200`（出现 `405` 即表示 POST 会被浏览器拦掉，界面却仍显示有数据）。

### T8 · 越界校验

把播放头拖到 A 之前按 `Alt+B`。

**通过**：画面浮层出现红色提示「B 要落在 A 与 C 之间」，`marks.b` 保持不变。

### T9 · 反向指令通道（试听）

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"want_preview":true}' http://127.0.0.1:8733/api/session
sleep 4
curl -s http://127.0.0.1:8733/api/session | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['want_preview'], d['pos'])"
```

**通过**：`want_preview` 回落到 `false`（指令已被插件取走），
`pos` 落在 A 点附近，且播放器开始播放，**IINA 不崩溃**（见下方第四个坑）。

> 这一条只在**桥不通**时才成立 —— 桥通之后插件不再轮询会话，`want_preview` 没人取。
> 桥通与否看 `@data/diag.json` 里的 `bridgeUp`（或 `bridge-check.json` 是否生成）。
> 桥通时直接点面板上的「试听」，判据是播放器跳回 A 点并开始播放，
> 且面板显示「正在播放 A→C」（这句经桥推给面板，不再经过会话的 `notice`）。

### T10 · 完整生成链路

```bash
# 1) 按本机路径导入（复制，不动原文件）
R=$(curl -s -X POST -H "Content-Type: application/json" \
  -d '{"path":"/绝对路径/视频.mp4"}' http://127.0.0.1:8733/api/open)
echo "$R"
J=$(echo "$R" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")

# 2) 触发合成
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"a":10,"b":25,"c":60,"text":"要补的旁白","mode":"sandwich","speed":1.0,"auto_ref":true,"pace":false,"unify_loudness":false,"qc":true}' \
  "http://127.0.0.1:8733/api/job/$J/generate"

# 3) 轮询直到阶段变为 done 或 error
curl -s "http://127.0.0.1:8733/api/job/$J"
```

**通过**：阶段依次经过 `extract` → `synth` → `qc`，最终 `done`，`error` 为 `null`。

成品的绝对路径在 `result.out_dir` / `result.out_wav` 里：

```bash
python3 -c "
import json; d=json.load(open('work/jobs/$J/result.json'))
print('成品目录 :', d['out_dir']); print('成品 wav :', d['out_wav'])"
```

面板会直接显示这段路径，并提供「在访达中显示」按钮（走 `POST /api/reveal`，
出于安全只接受输出目录内的路径）。

### T11 · 成品核验

```bash
python3 -c "
import json
d=json.load(open('work/jobs/$J/result.json'))
print('参考窗口 :', d['ref_window']['start'], '→', d['ref_window']['end'], '|', d['ref_window']['reason'])
print('面包区间 :', d['timing']['bread1'], d['timing']['bread2'])
print('响度对齐 :', d['loudness'])
print('成品时长 :', d['out_duration'])
print('回读文本 :', d['qc']['asr_text'], '| 相似度 :', d['qc']['similarity'])
"

# 直接测量三段与成品
for f in bread1_raw nar_raw bread2_raw talk_两面包夹芝士; do
  printf '%s\t%s\n' "$f" "$(ffprobe -v error -show_entries format=duration \
    -of default=nw=1:nk=1 work/jobs/$J/gen/$f.wav)"
done
```

**通过**：三段时长之和约等于成品时长；`ref_window` 给出实际用于采样的区间与理由；
`loudness` 中旁白已被对齐到面包的响度；回读文本与稿子一致。
输出目录同时提供 `.wav`（进剪辑）与 `.mp3`（试听）。

### T12 · 隐私核查（每次提交前）

```bash
git status --short --ignored
```

**通过**：`work/`、`bcb.local.env`、`.workbuddy/` 均在忽略列表内，
产物目录 `work/`（含上传素材与克隆结果）不得入库。

---

## 实测记录

2026-10-12，100 秒真人说话测试视频（`talk.mp4`，100.0 s，AAC 音轨），
按上方[测试与验收](#测试与验收)清单逐条跑出的结果：

| 项目 | 结果 |
|---|---|
| T3 入口执行步骤 | 11 项全部 `ok`，含 `sidebar.loadFile`、`sidebar.show` |
| T5 / T6 播放状态 | 文件名 `talk.mp4`，时长 `100`，位置 31.46 → 36.25（5 秒推进 4.79 秒） |
| T7 打点写入 | `{"a": 10, "b": 25, "c": 60}` 正确落库 |
| T9 反向指令 | 指令被插件取走（`want_preview` 回落 `false`），播放器跳至 A 点并续播，此后读得 12.33 s |
| T10 生成链路 | 阶段依次 `synth` → `qc` → `done`，`error` 为 `null` |
| 参考窗口（自动精选） | 13.5 → 25.5 s，理由为「区间 50.0 s 偏长，自动精选 12 s 子窗口（语音占比 69%）」 |
| 面包 A→B | 15.040 s |
| 合成旁白 | 3.553 s |
| 面包 B→C | 35.008 s |
| 成品总长 | **53.601 s**（53.601 ≈ 15.040 + 3.553 + 35.008） |
| 响度对齐 | 旁白原始 −23.17 LUFS → 对齐至面包 −23.46 LUFS，未整体压缩成品 |
| 回读质检 | 相似度 100%，回读文本与稿子逐字相符 |

2026-10-12 追加：面板按钮写入 `/api/session` 时日志持续刷
`OPTIONS … 405`，打点静默失效。已在后端挂 `CORSMiddleware`（`server/main.py`），
复测 `OPTIONS /api/session` 返回 `200`，跨源 POST 落库正常。

同日再追加：打点正常，但点面板的「试听」时 **IINA 崩溃**（SIGABRT）。
崩溃栈显示插件在 `iina.http` 的回调里调用了 `overlay.simpleMode()`，
而该回调位于 `NSURLSession-delegate` 后台队列，AppKit 不在主线程即 abort。
已把 http 回调路径上的提示改为写进会话 `notice`、由面板渲染；
后端 `SESSION` 新增 `notice` 字段。

---

## 已知限制

- **后端需手动启动**。尚未实现由插件自行拉起并在退出时回收；
  `global.js` 即为该功能预留的位置（已实测：由执行外部进程派生出的后台进程可持续存活）。
- **成品需手动取用**。面板会给出真实路径与「在访达中显示」，
  但尚未实现一键回到播放器播放成果，也不提供网页内下载。
- **素材限定本地文件**。`POST /api/open` 按路径复制，网络流地址无法适用。
- **面板依赖轮询**。状态刷新存在数百毫秒量级的延迟。
- **键盘焦点会被侧栏网页接走**。侧栏是 `WKWebView`，它成为 first responder 之后，
  空格这类不带修饰键的按键就落在网页里，播放器的 `keyDown` 收不到，表现是
  「敲空格没反应」。`Alt` / `Cmd` 一类菜单快捷键走 main menu，不受影响 ——
  这个差别可以用来区分是不是焦点问题。IINA 只在窗口重新变成 key window 时把焦点
  收回去（`MainWindowController.windowDidBecomeKey` 里的 `makeFirstResponder(window)`），
  所以救回来的办法是切走再切回、点一下画面，或在面板里按 `Esc`。
  插件侧已做的两件事：面板不主动弹出；焦点停在面板里（但不在输入框）时，
  空格转发给播放器（`want_pause_toggle`）。
- **改动需完全重启**。无论是插件还是后端，改完都要 Quit 后重开，仅关窗口无效。
- **插件标识为开发用值** `local.bread-cheese-bread`。对外发布前建议改为反向 DNS 形式。

---

## 排错

| 现象 | 检查方法 |
|---|---|
| 侧边栏无「夹芝士」标签 | 确认 `Info.json` 含 `sidebarTab`；确认 `PluginEnabled.local.bread-cheese-bread` 为 1；**完全退出** IINA 再打开 |
| 面板空白或无数据 | 先看 T3 的 `diag.json`，定位首个 `ok: false` 的步骤及其 `error` |
| 面板显示「未连到本机服务」 | 后端未启动，或 8733 端口被占用：`lsof -nP -iTCP:8733` |
| 快捷键无反应 | 与系统或其他应用的快捷键冲突时，逐个在 `main.js` 的 `MENU` 中改用其他组合 |
| 空格不暂停，但 `Alt+…` 快捷键正常 | 键盘焦点被侧栏网页接走了。点一下画面、或切走再切回即可；频繁出现就别让侧栏停在展开状态 |
| 后端返回 500 | 查看运行 `./run.sh` 的终端输出，接口处的 traceback 会指出具体行号 |
| 日志刷 `OPTIONS /api/session 405` | 面板的跨源预检被拒，POST 实际未发出。确认 `server/main.py` 已挂 `CORSMiddleware`，并**重启后端**；面板页面也需重新加载 |
| 点「试听」IINA 直接崩溃 | 插件在 `iina.http` 回调里碰了播放器 UI。检查崩溃栈是否含 `JavascriptAPIOverlay` 或 `pluginOverlayViewContainer`；该回调内只能做数据操作，提示要走会话的 `notice` 字段 |
| 修改了 `iina/` 但行为未变 | 重新执行 `./iina/install.sh`，并完全退出 IINA 后重启 |

### 开发期的五个致命坑

排查上述问题前，先排除以下五项，前四项表现为「静默失效」，第五项会直接崩溃：

1. **`sidebar.loadFile` 必须晚于播放器窗口就绪**。提前调用会抛出
   `sidebar.loadFile called when window is not available`，
   且后续代码一行都不会执行。挂载点是 `iina.event.on("iina.window-loaded", …)`。
   **并且 `onMessage` 必须排在 `loadFile` 之后** —— 它会清空已注册的监听，
   详见上文「桥的三个使用细节」。
2. **`iina.http.post` 的签名只有两个参数**：`post(url, { data, headers })`，返回 Promise。
   写成旧式的 `post(url, {}, data, cb, cb)` 时请求体为空，
   后端解析空体会得到 500，且日志不提示真实原因。
3. **面板页面与后端不同源，POST 会先被浏览器预检**。面板的加载源
   与 `http://127.0.0.1:8733` 不同源（IINA 的侧边栏走私有协议），
   带 `Content-Type: application/json` 的 POST 属于跨源请求，浏览器会先发 `OPTIONS`。
   后端没有 CORS 中间件时，
   预检落到路由上就是 `405 Method Not Allowed`，真正的 POST 根本不会发出，
   于是打点、试听、写稿全部无声失败 —— 而 `GET` 是简单请求不受影响，
   界面看起来「连上了、有数据」，极具迷惑性。
   修法在 `server/main.py` 挂载 `CORSMiddleware`（放开来源与方法即可，
   服务只监听回环地址）。放行后日志里的 `OPTIONS /api/session` 应为 `200 OK`。
4. **`iina.http` 的回调跑在后台队列，里面绝不能碰播放器 UI。**
   `get/post` 的回调线程是 `com.apple.NSURLSession-delegate`（非主线程），
   在这个线程里调用 `iina.overlay.*` 或 `iina.core.osd()` 会让 IINA **直接 abort**。
   实测崩溃栈：

   ```
   closure in JavascriptAPIHttp.request(_:url:options:)
     → JavascriptAPIOverlay.simpleMode()
     → MainWindowController.pluginOverlayViewContainer.getter
     → [NSView addSubview:positioned:relativeTo:]   ← NSISEngine 抛异常 → abort()
   ```

   首次调用 `overlay.simpleMode()` 才会崩（只有首次需要 `addSubview` 创建容器），
   所以现象是「前面几次还好好的，某一步突然崩」，极具迷惑性。
   菜单项与快捷键的回调在主线程，可以放心用 UI。
   **凡从 http 回调发起的动作，反馈一律写进会话的 `notice` 字段，由面板渲染。**
   IINA 未提供任何把代码派发回主线程的方法，也没有这类标注。
