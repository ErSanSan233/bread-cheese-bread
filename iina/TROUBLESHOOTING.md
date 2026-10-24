# 两面包夹芝士 · IINA 插件（排错与验收）

实现细节在 [`README.md`](README.md)，本文只放三样东西：诊断入口、排错表、
验收清单。

---

## 目录

1. [诊断入口](#诊断入口)
2. [排错表](#排错表)
3. [开发期的五个致命坑](#开发期的五个致命坑)
4. [验收清单 T1–T12](#验收清单-t1t12)
5. [实测记录](#实测记录)

---

## 诊断入口

插件里很多失败是**静默的** —— 沙箱会把脚本异常直接丢掉，界面上什么都不发生。
所以 `main.js` 每一步都单独兜错并写进诊断文件：

```bash
cat ~/Library/Application\ Support/com.colliderli.iina/plugins/.data/local.bread-cheese-bread/diag.json
```

> `@data` 的真实位置是 `plugins/.data/<identifier>/`，**不是**插件目录下的 `@data`。

关注三个字段：

| 字段 | 说明 |
|---|---|
| `log` | 每一步的 `{step, ok, error}`。找第一个 `ok: false`，`error` 就是失败原因 |
| `bridgeUp` | 消息桥是否已通（页面发过 `ui-ready`） |
| `gotUiReady` | 插件是否收到过页面的报到 |

常见组合：

| 组合 | 读法 |
|---|---|
| `loadFile` ok，`gotUiReady` false | 桥没接住。第一嫌疑是 `onMessage` 注册在了 `loadFile` 之前 |
| 两者都 false，且 `log` 里有 `sidebar.loadFile` 失败 | 调用得太早，窗口还没就绪 |
| 两者都 true，面板仍空白 | 看后端终端有没有请求；面板本身可能没被执行 |

后端侧的另一份证据是运行 `./run.sh` 的那个终端，每条请求都会打一行。

---

## 排错表

| 现象 | 检查方法 |
|---|---|
| 侧边栏无「夹芝士」标签 | 确认 `Info.json` 含 `sidebarTab`；确认 `PluginEnabled.local.bread-cheese-bread` 为 1；**完全退出** IINA 再打开 |
| 面板空白或无数据 | 先看 `diag.json`，定位首个 `ok: false` 的步骤及其 `error` |
| 面板显示「未连到本机服务」 | 后端未启动，或 8733 端口被占用：`lsof -nP -iTCP:8733` |
| 快捷键无反应 | 与系统或其他应用的快捷键冲突时，逐个在 `main.js` 的 `MENU` 中改用其他组合 |
| 空格不暂停，但 `Alt+…` 快捷键正常 | 键盘焦点被侧栏网页接走了。点一下画面、或切走再切回即可；频繁出现就别让侧栏停在展开状态 |
| 后端返回 500 | 查看运行 `./run.sh` 的终端输出，接口处的 traceback 会指出具体行号 |
| 日志刷 `OPTIONS /api/session 405` | 面板的跨源预检被拒，POST 实际未发出。确认 `server/main.py` 已挂 `CORSMiddleware`，并**重启后端**；面板页面也需重新加载 |
| 点「试听」IINA 直接崩溃 | 插件在 `iina.http` 回调里碰了播放器 UI。检查崩溃栈是否含 `JavascriptAPIOverlay` 或 `pluginOverlayViewContainer`；该回调内只能做数据操作，提示要走会话的 `notice` 字段 |
| 修改了 `iina/` 但行为未变 | 重新执行 `./iina/install.sh`，并完全退出 IINA 后重启 |
| 整机周期性卡 1~2 分钟 | 模型进程在加载约 10 GB 权重，属正常。别连续重启，也别强杀后端（会留下孤儿进程，下一轮启动时 `synth.reap_stale()` 会清理并打印清了几个） |
| 手动重载插件时 IINA 崩溃（SIGTRAP） | IINA 侧解包问题，JS 捕获不到。只在手动重载时触发，正常启动不受影响 |

---

## 开发期的五个致命坑

排查上表之前，先排除以下五项。前四项表现为**静默失效**，第五项会直接崩溃。

### 1. `sidebar.loadFile` 必须晚于播放器窗口就绪

提前调用会抛 `sidebar.loadFile called when window is not available`，
且后续代码一行都不会执行。挂载点是 `iina.event.on("iina.window-loaded", …)`。

**并且 `onMessage` 必须排在 `loadFile` 之后** —— `loadFile` 内部会调用
`messageHub.clearListeners()`，把此前注册的监听一次性清掉，而网页加载是异步的，
清监听必然发生在页面脚本执行之前。写成「先注册、再 loadFile」，页面发出来的消息
全部石沉大海，症状是 `diag.json` 里 `loadFile` 成功、`gotUiReady` 却是 false。

> 这个坑出现过两次，且两次原因不同：第一次是页面根本没发消息（没用桥），
> 第二次是插件没接住（监听被清）。**判断桥通不通不能只看有没有收到消息。**

### 2. `iina.http.post` 的签名只有两个参数

`post(url, { data, headers })`，返回 Promise。写成旧式的
`post(url, {}, data, cb, cb)` 时请求体为空，后端解析空体得到 500，
且日志不提示真实原因。

### 3. 面板页面与后端不同源，POST 会先被浏览器预检

面板的加载源与 `http://127.0.0.1:8733` 不同源（IINA 的侧边栏走私有协议），
带 `Content-Type: application/json` 的 POST 属于跨源请求，浏览器会先发 `OPTIONS`。
后端没有 CORS 中间件时，预检落到路由上就是 `405 Method Not Allowed`，
真正的 POST 根本不会发出，于是打点、试听、写稿全部无声失败 ——
而 `GET` 是简单请求不受影响，界面看起来「连上了、有数据」，极具迷惑性。

修法在 `server/main.py` 挂载 `CORSMiddleware`（放开来源与方法即可，服务只监听回环地址）。
放行后日志里的 `OPTIONS /api/session` 应为 `200 OK`。

### 4. `iina.http` 的回调跑在后台队列，里面绝不能碰播放器 UI

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
所以现象是「前面几次还好好的，某一步突然崩」。
菜单项与快捷键的回调在主线程，可以放心用 UI。
**凡从 http 回调发起的动作，反馈一律写进会话的 `notice` 字段，由面板渲染。**
IINA 未提供任何把代码派发回主线程的方法。

### 5. 音频路由必须支持 Range

`GET /media/...` 若不支持 `Range`，浏览器拿不到 206，进度条拖不动。
本站已在 `server/main.py` 实现；改动这块时要一并冒烟**页面 + 静态资源 + Range +
越权路径**，只测 API 会漏掉媒体路由。

---

## 验收清单 T1–T12

在装好插件、后端运行、播放器打开本地视频的前提下执行。每条给出判定依据。

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

**通过**：桥不通时该行持续出现，约每 1.2 秒一行（主循环周期）。
桥通之后**不应该**再有周期性的这一行 —— 那时状态走桥，网络请求只剩探测后端那几次。
终端里完全不出现时，按 [T3](#t3--main-入口每一步的执行情况) 检查面板是否加载成功。

### T5 · 播放状态是否正确上报

```bash
curl -s http://127.0.0.1:8733/api/session
```

**通过**：`alive` 为 `true`，`name` 为当前文件名，`dur` 为视频总秒数且大于 0。

> 桥通之后插件不再往会话写状态，这一条只在**桥不通**时才看得到东西。

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
`pos` 落在 A 点附近，且播放器开始播放，**IINA 不崩溃**（见坑 4）。

> 这一条只在**桥不通**时才成立 —— 桥通之后插件不再轮询会话，`want_preview` 没人取。
> 桥通时直接点面板上的「试听」，判据是播放器跳回 A 点并开始播放，
> 且面板显示「正在播放 A→C」（这句经桥推给面板，不再经过会话的 `notice`）。

### T10 · 完整生成链路

```bash
# 1) 按本机路径导入（复制，不动原文件）
R=$(curl -s -X POST -H "Content-Type: application/json" \
  -d '{"path":"/绝对路径/视频.mp4"}' http://127.0.0.1:8733/api/open)
echo "$R"
J=$(echo "$R" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")

# 2) 触发合成。mode 也可取 full / narration
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
print('模式     :', d.get('mode'))
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

`mode` 为 `narration` 时不会有两片面包：产物只有 `xxx_旁白.wav` / `_48k.wav` / `.mp3`，
`timing` 里只有 `synth_sec`，响度退到统一目标并在 `loudness.note` 里写明。

### T12 · 隐私核查（每次提交前）

```bash
git status --short --ignored
```

**通过**：`work/`、`bcb.local.env`、`.workbuddy/` 均在忽略列表内，
产物目录 `work/`（含上传素材与克隆结果）不得入库。

---

## 实测记录

2026-10-12，100 秒真人说话测试视频（`talk.mp4`，100.0 s，AAC 音轨），
按上方验收清单逐条跑出的结果：

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

2026-10-23：手动重载插件触发一次 SIGTRAP，栈为
`reloadAllPlugins → emit("iina.window-loaded") → sidebar.loadFile`，
IINA 侧解包问题，JS 层无法捕获。正常启动不受影响。
