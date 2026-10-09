"""两面包夹芝士 —— 本地 Web 服务。

只做调度：抽轨/切片/拼接用 ffmpeg，合成交给常驻的 IndexTTS2 进程，
回读质检交给 whisper。Web 层自身不装任何 ML 依赖。
"""
import os
import re
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.responses import StreamingResponse

from . import config, jobs, synth

config.ensure_dirs()
app = FastAPI(title="两面包夹芝士")

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
