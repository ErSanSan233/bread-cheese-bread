"""两面包夹芝士 —— 本地 Web 服务。

只做调度：抽轨/切片/拼接用 ffmpeg，合成交给常驻的 IndexTTS2 进程，
回读质检交给 whisper。Web 层自身不装任何 ML 依赖。
"""
import json
import os
import re
import shutil
import tempfile
import urllib.parse
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse

from . import config, jobs, synth

config.ensure_dirs()
app = FastAPI(title="两面包夹芝士")

# 播放器插件里的面板不是从本服务加载的（IINA 的侧边栏走 file:// 一类的私有源），
# 它向 127.0.0.1 发带 Content-Type: application/json 的 POST 属于跨源请求，
# 浏览器会先发一个 OPTIONS 预检。没有这段时预检落到路由上就是 405，
# 真正的 POST 会被浏览器直接拦掉 —— 面板的打点/试听会静默失效。
# 本机服务，只监听回环地址，这里放开全部来源与方法即可。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=86400,
)

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _range_response_with(path: Path, rng: str | None, size: int) -> Response:
    start, end = 0, size - 1
    status = 200
    headers = {"Accept-Ranges": "bytes"}
    if rng:
        m = RANGE_RE.match(rng.strip())
        if m:
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
            if start > end or start >= size:
                return Response(status_code=416, headers={
                    "Content-Range": f"bytes */{size}"})
            status = 206
    length = end - start + 1
    headers.update({
        "Content-Length": str(length),
        "Content-Type": _mime(path),
    })
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    def body():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1 << 20, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    # 必须走 StreamingResponse：Starlette 的 Response.render 只吃 bytes/str，
    # 同步生成器会被当成字符串去 .encode() 然后 500。
    return StreamingResponse(body(), status_code=status, headers=headers,
                             media_type=_mime(path))


def _mime(p: Path) -> str:
    return {".wav": "audio/wav", ".mp3": "audio/mpeg",
            ".mp4": "video/mp4", ".json": "application/json"}.get(
        p.suffix.lower(), "application/octet-stream")


@app.on_event("startup")
def _startup():
    synth.warmup()


@app.on_event("shutdown")
def _shutdown():
    synth.shutdown()


@app.get("/", response_class=HTMLResponse)
def index():
    return (config.WEB / "index.html").read_text(encoding="utf-8")


@app.get("/app.js")
def app_js():
    return Response((config.WEB / "app.js").read_text(encoding="utf-8"),
                    media_type="application/javascript")


@app.get("/style.css")
def style_css():
    return Response((config.WEB / "style.css").read_text(encoding="utf-8"),
                    media_type="text/css")


@app.get("/api/health")
def health():
    st = config.preflight()
    return {"ok": not st["missing"], **st}


# 播放器会话：IINA 插件与播放器内的面板之间无法直接通信
# （本机 IINA 1.5.0 的 webview 未向这两处注入可用的双向桥），
# 但两者都能访问本机后端，所以由后端充当它们的中转。
SESSION: dict = {
    "path": None,     # 正在播放的媒体文件路径
    "name": None,     # 文件名，用于界面显示
    "pos": 0.0,       # 当前播放位置（秒）
    "dur": 0.0,       # 总时长
    "marks": {"a": None, "b": None, "c": None},
    "text": "",       # 面板里写的旁白稿
    "job": None,      # 面板正在跟踪的生成任务
    "alive": False,   # 插件最近是否上报过状态
    # 以下两个是面板给插件下的指令，插件轮询取走后清零
    "want_preview": False,   # 试听 A→C
    "want_goto": None,       # 跳到指定秒
}


async def _read_body(req: Request):
    """把请求体解析成 dict。

    插件侧用什么编码取决于 IINA 的实现，可能会是 JSON，也可能空着或表单编码。
    这里统一兜住，避免对方发了个空体就把整个接口打成 500。
    """
    raw = await req.body()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    try:
        parsed = json.loads(urllib.parse.parse_qs(raw.decode("utf-8", "replace")).get("data", [""])[0] or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


@app.get("/api/session")
def session_get():
    """面板轮询这里拿播放器状态；插件不需要轮询，状态由它主动推送。"""
    return {"ok": True, **SESSION}


@app.post("/api/session")
async def session_post(req: Request):
    """插件上报播放状态；面板写回旁白稿与打点。两者共用这一个端点。"""
    body = await _read_body(req)
    if not isinstance(body, dict):
        body = {}
    for key in ("path", "name", "pos", "dur", "marks", "text", "alive",
                "want_preview", "want_goto"):
        if key not in body:
            continue
        if key == "marks" and isinstance(body["marks"], dict):
            # 快捷键（插件）和点按钮（面板）都会写打点，两者互不感知，
            # 这里按字段合并，免得一方把另一方刚打好的点整片覆盖掉。
            for slot in ("a", "b", "c"):
                if slot in body["marks"]:
                    SESSION["marks"][slot] = body["marks"][slot]
        else:
            SESSION[key] = body[key]
    if "path" in body and body["path"]:
        SESSION["alive"] = True
    return {"ok": True, **SESSION}


@app.post("/api/open")
async def open_path(req: Request):
    """按本机路径打开素材（用于播放器场景，不接受上传的文件流）。

    与 /api/import 的唯一区别是：源在这里是复制而非移动 ——
    用户播放器里的原文件不能被拿走。
    """
    body = await req.json()
    src = Path(str(body.get("path") or "")).expanduser()
    if not src.is_file():
        return JSONResponse({"ok": False, "error": f"文件不存在：{src}"},
                            status_code=404)
    tmpdir = Path(tempfile.mkdtemp(prefix="bcb_src_", dir=str(config.WORK)))
    tmp = tmpdir / ("src" + (src.suffix.lower() or ".bin"))
    try:
        shutil.copyfile(src, tmp)
        job = jobs.create_job(tmp, src.name)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    if job["status"]["stage"] == "error":
        return JSONResponse({"ok": False, **job["status"], "id": job["id"]},
                            status_code=400)
    return {"ok": True, "id": job["id"], "name": job["name"],
            "stem": job["stem"], "duration": job["info"]["duration"],
            "has_video": job["info"].get("has_video", False)}


@app.post("/api/import")
async def import_file(file: UploadFile = File(...)):
    suffix = Path(file.filename or "audio").suffix or ".bin"
    tmpdir = Path(tempfile.mkdtemp(prefix="bcb_up_", dir=str(config.WORK)))
    tmp = tmpdir / f"src{suffix}"
    try:
        with tmp.open("wb") as f:
            shutil.copyfileobj(file.file, f, length=1 << 22)
        job = jobs.create_job(tmp, file.filename or "audio")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    if job["status"]["stage"] == "error":
        return JSONResponse({"ok": False, **job["status"], "id": job["id"]},
                            status_code=400)
    return {
        "ok": True, "id": job["id"], "name": job["name"], "stem": job["stem"],
        "duration": job["info"]["duration"],
        "sample_rate": job["info"].get("sample_rate"),
        "channels": job["info"].get("channels"),
        "has_video": job["info"].get("has_video", False),
    }


@app.get("/api/job/{job_id}")
def job_state(job_id: str):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return {"id": job["id"], "name": job["name"], "stem": job["stem"],
            "info": job.get("info", {}), **job["status"]}


@app.get("/api/job/{job_id}/waveform")
def waveform(job_id: str):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return jobs.waveform(job)


@app.post("/api/job/{job_id}/analyze")
async def analyze(job_id: str, req: Request):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    body = await req.json()
    try:
        return {"ok": True, **jobs.analyze(
            job, float(body["a"]), float(body["b"]), float(body["c"]))}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/api/job/{job_id}/transcribe-ref")
async def transcribe_ref(job_id: str, req: Request):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    body = await req.json()
    try:
        text = jobs.transcribe_reference(
            job, float(body["start"]), float(body["end"]))
        return {"ok": True, "text": text}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/api/job/{job_id}/generate")
async def generate(job_id: str, req: Request):
    job = jobs.get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    if job["status"]["stage"] not in ("ready", "done", "error", "queue"):
        return JSONResponse({"ok": False, "error": "上一次生成还没结束"},
                            status_code=409)
    body = await req.json()
    jobs.start_generate(job, body)
    return {"ok": True}


@app.get("/media/{job_id}/{rest:path}")
def media(job_id: str, rest: str, request: Request):
    base = (config.WORK / "jobs" / job_id).resolve()
    p = (base / rest).resolve()
    if not str(p).startswith(str(base) + os.sep) or not p.is_file():
        raise HTTPException(404, "文件不存在")
    size = p.stat().st_size
    return _range_response_with(p, request.headers.get("range"), size)
