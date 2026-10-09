"""从用户框定的 AC 区间里挑一段真正适合当音色参考的窗口。

为什么需要这一步：
IndexTTS2 的参考音频在 8~15s 效果最稳，太短信息量不够、太长会把语气拖杂。
用户框 AC 时往往框得比实际需要的宽，所以这里自动内缩一层。

判据（沿袭此前实测的优先级）：削波率最低 > 时长 8~15s > 段内连续语音、无长静音
> 尽量包含插入点 B（语气要跟插入点周围对得上）。
"""
from pathlib import Path

import numpy as np

from . import audio, config

_HOP = 0.02          # 分析帧长（秒）
_SILENCE_OK = 0.7    # 段内允许的连续静音上限（秒）


def _frames(x: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray]:
    """返回 (每帧 RMS, 每帧最大绝对值)。"""
    n = int(_HOP * rate)
    if n <= 0 or x.size < n:
        raise ValueError("区间太短，无法分析")
    m = x.size // n
    x = x[: m * n].reshape(m, n)
    return np.sqrt((x ** 2).mean(axis=1)), np.abs(x).max(axis=1)


def analyze_region(ref_wav: Path, a: float, b: float, c: float,
                   rate: int = config.REF_RATE) -> dict:
    x = audio.decode_f32(ref_wav, start=a, end=c, rate=rate)
    if x.size == 0:
        raise ValueError("区间内没有音频")
    region_len = c - a
    rms, peak = _frames(x, rate)
    ref_rms = float(np.sqrt((x ** 2).mean()))
    thr = max(ref_rms * 0.25, 1e-4)
    speech = rms > thr

    whole = {
        "length": round(region_len, 3),
        "speech_ratio": round(float(speech.mean()), 3),
        "clip_ratio": round(float((peak >= 0.99).mean()), 4),
        "rms": round(ref_rms, 5),
    }

    warnings: list[str] = []
    if region_len < config.REF_WINDOW_MIN:
        warnings.append(
            f"区间只有 {region_len:.1f}s，短于 {config.REF_WINDOW_MIN}s，音色信息可能不足")

    if region_len <= config.REF_WINDOW_MAX:
        return {
            "start": round(a, 3),
            "end": round(c, 3),
            "auto": False,
            "reason": f"区间 {region_len:.1f}s 在 {config.REF_WINDOW_MAX}s 以内，整段直接用作参考",
            "speech_ratio": whole["speech_ratio"],
            "clip_ratio": whole["clip_ratio"],
            "whole": whole,
            "warnings": warnings,
        }

    b_rel = b - a
    best = None
    for li, L in enumerate(config.REF_WINDOW_CANDIDATES):
        if L > region_len - 0.2:
            continue
        n_frames = int(L / _HOP)
        starts = np.arange(0.0, region_len - L + 1e-6, 0.5)
        for t0 in starts:
            i0 = int(t0 / _HOP)
            i1 = min(i0 + n_frames, rms.size)
            if i1 - i0 < n_frames * 0.8:
                continue
            sp = speech[i0:i1]
            pk = peak[i0:i1]
            speech_ratio = float(sp.mean())
            clip_ratio = float((pk >= 0.99).mean())
            # 段内最长连续静音
            longest, run = 0.0, 0.0
            for v in sp:
                run = 0.0 if v else run + _HOP
                longest = max(longest, run)
            pen = max(0.0, longest - _SILENCE_OK) / 2.0
            score = speech_ratio - 4.0 * clip_ratio - pen
            if t0 <= b_rel <= t0 + L:
                score += 0.15          # 含插入点 B，语气更贴上下文
            score += 0.05 * (len(config.REF_WINDOW_CANDIDATES) - li)
            if best is None or score > best[0]:
                best = (score, float(t0), L, speech_ratio, clip_ratio, longest)

    if best is None:
        return {
            "start": round(a, 3), "end": round(c, 3), "auto": False,
            "reason": "没能算出更好的子窗口，退回整段",
            "speech_ratio": whole["speech_ratio"],
            "clip_ratio": whole["clip_ratio"],
            "whole": whole, "warnings": warnings,
        }

    _, t0, L, sr, cr, longest = best
    if sr < 0.5:
        warnings.append(
            f"精选窗内语音占比仅 {sr:.0%}，区间可能有大量静音或纯音乐")
    if cr > 0.01:
        warnings.append(f"精选窗内有 {cr:.2%} 采样削波，音色可能发毛")

    return {
        "start": round(a + t0, 3),
        "end": round(a + t0 + L, 3),
        "auto": True,
        "reason": f"区间 {region_len:.1f}s 偏长，自动精选 {L:.0f}s 子窗口"
                  f"（语音占比 {sr:.0%}，最长静音 {longest:.1f}s）",
        "speech_ratio": round(sr, 3),
        "clip_ratio": round(cr, 4),
        "whole": whole,
        "warnings": warnings,
    }


def make_reference(ref_wav: Path, out: Path, start: float, end: float) -> None:
    audio.slice_audio(ref_wav, start, end, out)
