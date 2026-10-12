"""任务状态与生成流水线。

一次生成的完整流程：
  A→B（面包） + [AC 区间取音色 → 合成旁白] + B→C（面包）
旁白的响度去匹配面包，原始片段本身不做任何处理。
"""
import json
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import audio, config, pace, qc, refpick, synth

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _dir(job_id: str) -> Path:
    return config.WORK / "jobs" / job_id


def create_job(src_path: Path, original_name: str) -> dict:
    config.ensure_dirs()
    job_id = uuid.uuid4().hex[:12]
    d = _dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "gen").mkdir(exist_ok=True)

    ext = src_path.suffix.lower() or ".bin"
    saved = d / f"source{ext}"
    shutil.move(str(src_path), str(saved))

    job = {
        "id": job_id,
        "name": Path(original_name).name,
        "stem": Path(original_name).stem or "audio",
        "dir": d,
        "status": {"stage": "extract", "progress": 0.05,
                   "message": "正在抽音轨…", "error": None, "result": None},
    }
    try:
        info = audio.extract_audio(saved, d / "hifi.wav", d / "ref32.wav")
        job["info"] = info
        job["status"] = {"stage": "ready", "progress": 1.0,
                         "message": "就绪", "error": None, "result": None}
    except Exception as e:
        job["info"] = {"duration": 0}
        job["status"] = {"stage": "error", "progress": 0.0,
                         "message": "导入失败", "error": str(e), "result": None}
    with _lock:
        _jobs[job_id] = job
    return job


def get_job(job_id: str) -> dict | None:
    with _lock:
        return _jobs.get(job_id)


def set_status(job: dict, stage: str, progress: float, message: str):
    job["status"].update({"stage": stage, "progress": round(progress, 3),
                          "message": message, "error": None})


def fail(job: dict, message: str, err: Exception | str):
    job["status"].update({"stage": "error", "progress": 0.0,
                          "message": message,
                          "error": f"{err}", "result": None})
    (job["dir"] / "error.log").write_text(
        traceback.format_exc() if isinstance(err, Exception) else str(err),
        encoding="utf-8")


def waveform(job: dict) -> dict:
    cache = job["dir"] / "peaks.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    peaks = audio.waveform_peaks(job["dir"] / "ref32.wav")
    data = {"peaks": peaks, "duration": job["info"]["duration"],
            "sample_rate": job["info"].get("sample_rate"),
            "channels": job["info"].get("channels"),
            "has_video": job["info"].get("has_video", False)}
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def analyze(job: dict, a: float, b: float, c: float) -> dict:
    pick = refpick.analyze_region(job["dir"] / "ref32.wav", a, b, c)
    return pick


def transcribe_reference(job: dict, start: float, end: float,
                         out_name: str = "ref_asr.wav") -> str:
    w = job["dir"] / out_name
    audio.slice_audio(job["dir"] / "ref32.wav", start, end, w)
    return qc.transcribe(w)


def _run_pipeline(job: dict, req: dict):
    d = job["dir"]
    gen = d / "gen"
    a, b, c = float(req["a"]), float(req["b"]), float(req["c"])
    text = (req.get("text") or "").strip()
    mode = req.get("mode") or "sandwich"
    speed = float(req.get("speed") or 1.0)
    auto_ref = bool(req.get("auto_ref", True))
    unify = bool(req.get("unify_loudness", False))
    want_qc = bool(req.get("qc", True))
    dur = job["info"]["duration"]

    if not text:
        raise ValueError("旁白文本是空的")
    if not (0 <= a < b < c <= dur + 0.001):
        raise ValueError(f"时间点不满足 A<B<C 且在音频范围内（总长 {dur:.2f}s）")

    hifi = d / "hifi.wav"
    info = audio.probe(hifi)
    rate, ch = info["sample_rate"] or 48000, info["channels"] or 1
    stem = job["stem"]

    # 1. 参考窗口
    set_status(job, "ref", 0.08, "正在挑音色参考窗口…")
    if auto_ref:
        pick = refpick.analyze_region(d / "ref32.wav", a, b, c)
    else:
        pick = {"start": a, "end": c, "auto": False,
                "reason": "按用户指定，整段 AC 作为参考",
                "speech_ratio": None, "clip_ratio": None, "warnings": []}
    ref_wav = gen / "ref.wav"
    refpick.make_reference(d / "ref32.wav", ref_wav, pick["start"], pick["end"])

    # 2. 合成旁白（这一步最慢）
    set_status(job, "synth", 0.15, "正在合成旁白（首次需加载模型，约 1~2 分钟）…")
    t0 = time.time()
    nar_raw = gen / "nar_raw.wav"
    want_pace = bool(req.get("pace", True))
    pace_info: dict = {"enabled": want_pace}
    if want_pace:
        # 两遍法：先用原速出一版，测出它跟面包的语速差，再按系数重生成。
        # duration_factor 在模型里是线性缩放，所以系数一次就能算准。
        nar_v1 = gen / "nar_raw_v1.wav"
        synth.synth(ref_wav, text, nar_v1, speed=speed)
        set_status(job, "pace", 0.42, "正在测量面包语速…")
        try:
            ref_rate = pace.measure(ref_wav)
            nar_rate = pace.measure(nar_v1, text=text)
            factor, why = pace.suggest_factor(nar_rate, ref_rate)
            pace_info.update({"ref": ref_rate, "narration_v1": nar_rate,
                              "factor": factor, "reason": why,
                              "regenerated": False})
            if abs(factor - 1.0) >= 0.05:
                set_status(job, "pace2", 0.46, f"{why}，正在按新语速重生成…")
                synth.synth(ref_wav, text, nar_raw, speed=speed * factor)
                pace_info["regenerated"] = True
            else:
                shutil.copyfile(nar_v1, nar_raw)
        except Exception as e:
            # 测不出来就用第一遍，别为了一个增强项把整条流水线拖挂
            pace_info["error"] = str(e)
            shutil.copyfile(nar_v1, nar_raw)
    else:
        synth.synth(ref_wav, text, nar_raw, speed=speed)
    synth_sec = time.time() - t0

    # 3. 切两片面包
    set_status(job, "slice", 0.62, "正在切分原声…")
    if mode == "full":
        s1_src, s3_src = (0.0, b), (b, dur)
    else:
        s1_src, s3_src = (a, b), (b, c)
    b1_raw, b2_raw = gen / "bread1_raw.wav", gen / "bread2_raw.wav"
    audio.slice_audio(hifi, s1_src[0], s1_src[1], b1_raw)
    audio.slice_audio(hifi, s3_src[0], s3_src[1], b2_raw)

    # 4. 响度：旁白去匹配面包
    set_status(job, "loud", 0.72, "正在匹配响度…")
    s1, s3 = gen / "s1.wav", gen / "s3.wav"
    audio.standardize(b1_raw, s1, rate, ch)
    audio.standardize(b2_raw, s3, rate, ch)
    breads = gen / "_breads.wav"
    audio.concat_segments([s1, s3], breads)
    try:
        meas_b = audio.measure_loudness(breads)
        target_i = meas_b["input_i"]
    except Exception:
        meas_b, target_i = None, config.TARGET_LUFS
    if unify:
        target_i = config.TARGET_LUFS

    meas_n = audio.measure_loudness(nar_raw)
    s2 = gen / "s2.wav"
    audio.loudnorm_apply(nar_raw, s2, meas_n, target_i, rate, ch)

    # 5. 拼接
    set_status(job, "concat", 0.82, "正在拼接…")
    out_wav = gen / f"{stem}_两面包夹芝士.wav"
    audio.concat_segments([s1, s2, s3], out_wav)

    nar_wav = gen / f"{stem}_旁白.wav"
    shutil.copyfile(s2, nar_wav)
    nar_48k = gen / f"{stem}_旁白_48k.wav"
    try:
        audio.loudnorm_apply(nar_raw, nar_48k,
                             audio.measure_loudness(nar_raw),
                             config.TARGET_LUFS, 48000, 1)
    except Exception:
        nar_48k = None

    # 6. mp3
    set_status(job, "export", 0.9, "正在导出 mp3…")
    out_mp3 = gen / f"{stem}_两面包夹芝士.mp3"
    try:
        audio.to_mp3(out_wav, out_mp3)
    except Exception:
        out_mp3 = None

    # 7. 质检回读
    qcres = None
    if want_qc:
        set_status(job, "qc", 0.94, "正在回读质检…")
        try:
            got = qc.transcribe(nar_raw)
            qcres = qc.compare(text, got)
            qcres["asr_text"] = got
        except Exception as e:
            qcres = {"error": str(e)}

    result = {
        # 面板要显示「成品在哪」，给的是本机绝对路径（只在回环接口里出现）
        "out_dir": str(gen),
        "out_wav": str(out_wav),
        "out_mp3": str(out_mp3) if out_mp3 else None,
        "wav": f"/media/{job['id']}/gen/{out_wav.name}" if out_wav.exists() else None,
        "mp3": f"/media/{job['id']}/gen/{out_mp3.name}" if out_mp3 and out_mp3.exists() else None,
        "narration": f"/media/{job['id']}/gen/{nar_wav.name}",
        "narration_48k": (f"/media/{job['id']}/gen/{nar_48k.name}"
                          if nar_48k and nar_48k.exists() else None),
        "ref_window": pick,
        "ref_audio": f"/media/{job['id']}/gen/{ref_wav.name}",
        "timing": {
            "bread1": [round(s1_src[0], 3), round(s1_src[1], 3)],
            "bread2": [round(s3_src[0], 3), round(s3_src[1], 3)],
            "synth_sec": round(synth_sec, 1),
        },
        "loudness": {
            "bread_i": round(meas_b["input_i"], 2) if meas_b else None,
            "narration_raw_i": round(meas_n["input_i"], 2),
            "narration_matched_to": round(target_i, 2),
            "unified": unify,
        },
        "qc": qcres,
        "pace": pace_info,
        "out_duration": round(audio.duration_of(out_wav), 3),
    }
    set_status(job, "done", 1.0, "完成")
    job["status"]["result"] = result
    (d / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                   encoding="utf-8")


def start_generate(job: dict, req: dict):
    set_status(job, "queue", 0.02, "排队中…")
    job["status"]["result"] = None

    def _go():
        try:
            _run_pipeline(job, req)
        except Exception as e:
            traceback.print_exc()
            fail(job, "生成失败", e)
    threading.Thread(target=_go, daemon=True).start()
