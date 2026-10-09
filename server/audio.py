"""ffmpeg 封装：抽音轨 / 切片 / 响度测量与匹配 / 拼接。

关键取舍：
- 「面包」部分尽量不动原始音频 —— 抽轨用 pcm_s16le 保原采样率与声道，切片用 -c copy。
- 旁白的响度去**匹配面包**，而不是反过来把成品整体压 loudnorm。
  这样插回原视频时原始段落的电平不会被改动。
"""
import json
import re
import shlex
import subprocess
from pathlib import Path

import numpy as np

from . import config


class AudioError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def probe(path: Path) -> dict:
    r = _run([
        config.FFPROBE, "-v", "error", "-show_entries",
        "format=duration,bit_rate:stream=codec_type,codec_name,sample_rate,channels",
        "-of", "json", str(path),
    ])
    if r.returncode != 0:
        raise AudioError(f"ffprobe 失败: {r.stderr.strip()}")
    data = json.loads(r.stdout)
    fmt = data.get("format", {})
    out = {
        "duration": float(fmt.get("duration") or 0.0),
        "sample_rate": None,
        "channels": None,
        "has_video": False,
        "codec": None,
    }
    for st in data.get("streams", []):
        if st.get("codec_type") == "audio" and out["sample_rate"] is None:
            out["sample_rate"] = int(st.get("sample_rate") or 0) or None
            out["channels"] = int(st.get("channels") or 0) or None
            out["codec"] = st.get("codec_name")
        if st.get("codec_type") == "video":
            out["has_video"] = True
    if out["sample_rate"] is None:
        raise AudioError("没有找到音频流")
    return out


def extract_audio(src: Path, out_hifi: Path, out_ref: Path) -> dict:
    """从音视频里抽两条音轨：
    - hifi：原始采样率/声道，pcm_s16le，用于切「面包」
    - ref ：32k 单声道，用于给模型当音色参考
    """
    info = probe(src)
    r = _run([
        config.FFMPEG, "-hide_banner", "-y", "-i", str(src), "-vn",
        "-map", "0:a:0", "-c:a", "pcm_s16le", str(out_hifi),
    ])
    if r.returncode != 0:
        raise AudioError(f"抽音轨失败: {r.stderr[-800:]}")
    r2 = _run([
        config.FFMPEG, "-hide_banner", "-y", "-i", str(out_hifi),
        "-ac", "1", "-ar", str(config.REF_RATE), "-c:a", "pcm_s16le", str(out_ref),
    ])
    if r2.returncode != 0:
        raise AudioError(f"生成参考音轨失败: {r2.stderr[-800:]}")
    info["duration"] = probe(out_hifi)["duration"]
    return info


def slice_audio(src: Path, start: float, end: float, out: Path) -> None:
    if end - start <= 0.01:
        raise AudioError("切片长度过小")
    r = _run([
        config.FFMPEG, "-hide_banner", "-y", "-ss", f"{start:.6f}",
        "-to", f"{end:.6f}", "-i", str(src), "-c", "copy", str(out),
    ])
    if r.returncode != 0:
        raise AudioError(f"切片失败: {r.stderr[-600:]}")


def decode_f32(path: Path, start: float | None = None,
               end: float | None = None, rate: int = config.REF_RATE) -> np.ndarray:
    """解码成 float32 单声道数组，用于波形与能量分析。"""
    cmd = [config.FFMPEG, "-hide_banner", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.6f}"]
    if end is not None:
        cmd += ["-to", f"{end:.6f}"]
    cmd += ["-i", str(path), "-ac", "1", "-ar", str(rate),
            "-f", "f32le", "-c:a", "pcm_f32le", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    if r.returncode != 0:
        raise AudioError(f"解码失败: {r.stderr.decode(errors='ignore')[-600:]}")
    return np.frombuffer(r.stdout, dtype="<f4").astype(np.float32)


def waveform_peaks(path: Path, n: int = config.WAVE_PEAKS) -> list[float]:
    x = decode_f32(path, rate=16000)  # 波形只需低频分辨率，16k 足够且快
    if x.size == 0:
        return []
    n = min(n, x.size)
    step = x.size / n
    idx = (np.arange(n) * step).astype(np.int64)
    idx_end = np.minimum(idx + max(1, int(step)), x.size)
    out = np.empty(n, dtype=np.float32)
    for i in range(n):
        seg = np.abs(x[idx[i]:idx_end[i]])
        out[i] = seg.max() if seg.size else 0.0
    mx = float(out.max()) or 1.0
    return [round(float(v / mx), 4) for v in out]


def measure_loudness(path: Path) -> dict:
    """第一遍 loudnorm，只测量不改文件。"""
    r = _run([
        config.FFMPEG, "-hide_banner", "-i", str(path),
        "-af", f"loudnorm=I={config.TARGET_LUFS}:TP={config.TARGET_TP}"
               f":LRA={config.TARGET_LRA}:print_format=json",
        "-f", "null", "-",
    ])
    txt = r.stderr or ""
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", txt, flags=re.S)
    if not blocks:
        raise AudioError("响度测量失败（没拿到 loudnorm JSON）")
    d = json.loads(blocks[-1])
    return {
        "input_i": float(d["input_i"]),
        "input_tp": float(d["input_tp"]),
        "input_lra": float(d["input_lra"]),
        "input_thresh": float(d["input_thresh"]),
    }


def loudnorm_apply(src: Path, dst: Path, meas: dict, target_i: float,
                   rate: int, channels: int) -> None:
    """第二遍 loudnorm，带实测值，做精确匹配。"""
    # 有 measured_* 时增益由 loudnorm 自己算，offset 必须留 0，
    # 否则会在已算好的补偿上再叠一层，反而对不上目标。
    af = (
        f"loudnorm=I={target_i:.2f}:TP={config.TARGET_TP}:LRA={config.TARGET_LRA}"
        f":measured_I={meas['input_i']:.2f}"
        f":measured_TP={meas['input_tp']:.2f}"
        f":measured_LRA={meas['input_lra']:.2f}"
        f":measured_thresh={meas['input_thresh']:.2f}"
        f":offset=0.0:linear=true:print_format=summary"
    )
    r = _run([
        config.FFMPEG, "-hide_banner", "-y", "-i", str(src), "-af", af,
        "-ar", str(rate), "-ac", str(channels), "-c:a", "pcm_s16le", str(dst),
    ])
    if r.returncode != 0:
        raise AudioError(f"响度匹配失败: {r.stderr[-600:]}")


def standardize(src: Path, dst: Path, rate: int, channels: int) -> None:
    """统一成 pcm_s16le / 指定采样率与声道，供 concat 使用。"""
    r = _run([
        config.FFMPEG, "-hide_banner", "-y", "-i", str(src),
        "-ar", str(rate), "-ac", str(channels), "-c:a", "pcm_s16le", str(dst),
    ])
    if r.returncode != 0:
        raise AudioError(f"格式统一失败: {r.stderr[-600:]}")


def concat_segments(files: list[Path], dst: Path) -> None:
    """无损拼接（各段格式需一致，调用前已 standardize）。"""
    lst = dst.with_suffix(".concat.txt")
    lst.write_text(
        "".join(f"file {shlex.quote(str(f.resolve()))}\n" for f in files),
        encoding="utf-8",
    )
    r = _run([
        config.FFMPEG, "-hide_banner", "-y", "-f", "concat", "-safe", "0",
        "-i", str(lst), "-c", "copy", str(dst),
    ])
    if r.returncode != 0:
        raise AudioError(f"拼接失败: {r.stderr[-600:]}")


def to_mp3(src: Path, dst: Path) -> None:
    r = _run([
        config.FFMPEG, "-hide_banner", "-y", "-i", str(src),
        "-codec:a", "libmp3lame", "-b:a", "320k", str(dst),
    ])
    if r.returncode != 0:
        raise AudioError(f"mp3 转码失败（缺 libmp3lame？）: {r.stderr[-400:]}")


def duration_of(path: Path) -> float:
    return probe(path)["duration"]
