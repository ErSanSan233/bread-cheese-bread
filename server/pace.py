"""语速测量：量出旁白与面包的语速差，给手动滑条一个建议值。

为什么只测量、不自动重生成：试过两遍法（先合成一版、算系数、再重生成），
实测对齐到残差 0.1% 之后听感并没有变好，代价是每次多花一倍时间
（同一份 63 字稿件，合成从 163 秒涨到 622 秒）。自动对齐既然不解决问题，
就把数测出来交给人，用滑条自己调。

    factor = 旁白语速 / 面包语速

factor 是 duration_factor 意义上的倍数（>1 表示要放慢）；
界面上的 speed 是越大越快，两者换算时是除不是乘。

## 用哪个口径算「语速」

实测两组素材（2026-10-12）发现两件事：

- **发声速率**（字数 ÷ 去掉静音后的发声时长）：面包 8.21 / 7.87，旁白 8.58 / 7.93，
  只差 1%~4%。模型本来就把吐字快慢学得很像。
- **整体速率**（字数 ÷ 窗口总时长，含换气与停顿）：面包 4.96 / 4.83，旁白 5.85 / 4.31，
  差 11%~18%。

也就是说，**对不上的是「停顿占多少」，不是吐字快慢**。听感上的语速是后者，
所以默认按整体速率对齐。只有当参考窗口里大半不是语音（背景乐、环境底噪）
时总时长才是被污染的量，那种情况下退回发声速率。

## 边界

- 只量得出平均语速。句内的起伏、哪个词拖长，那一层是 GPT 决定的，这里动不了。
- factor 钳在 [0.7, 1.6]，换算到滑条上就是语速 0.63~1.43 倍。
"""
from pathlib import Path

import numpy as np

from . import audio, config, qc, refpick

LO = 0.7
HI = 1.6
MIN_VOICE_RATIO = 0.35   # 低于此值认为参考窗口里的「总时长」不可信


def speech_seconds(path: Path, rate: int = config.REF_RATE) -> float:
    """纯发声时长（秒）。

    VAD 口径与 `refpick` 完全一致（同一套分帧、同一条阈值），
    这样 result.json 里的 speech_ratio 和这里的发声时长能对得上。
    """
    x = audio.decode_f32(path, rate=rate)
    if x.size == 0:
        raise ValueError(f"没有读到音频：{path}")
    rms, _ = refpick._frames(x, rate)
    thr = max(float(np.sqrt((x ** 2).mean())) * 0.25, 1e-4)
    return float((rms > thr).sum()) * refpick._HOP


def measure(wav: Path, text: str | None = None) -> dict:
    """测一段音频的语速，两种口径都给，由 suggest_factor 决定用哪个。

    text 给了就不跑 ASR（旁白的字数直接用稿子）；
    没给就转一遍（测面包参考窗口时走这条路）。
    """
    voice_sec = speech_seconds(wav)
    total_sec = audio.probe(wav)["duration"]
    if text is None:
        text = qc.transcribe(wav)
    chars = len(qc.normalize(text))
    return {
        "chars": chars,
        "total_sec": round(total_sec, 3),
        "voice_sec": round(voice_sec, 3),
        "voice_ratio": round(voice_sec / total_sec, 3) if total_sec >= 0.5 else None,
        "rate_voice": round(chars / voice_sec, 3) if voice_sec >= 0.5 and chars else None,
        "rate_total": round(chars / total_sec, 3) if total_sec >= 0.5 and chars else None,
    }


def suggest_factor(narration: dict, ref: dict,
                   lo: float = LO, hi: float = HI) -> tuple[float, str]:
    """返回 (推荐的 duration_factor, 一句人话的原因)。

    参数是两个 measure() 的结果。
    """
    by_total = bool(narration.get("rate_total")) and bool(ref.get("rate_total")) \
        and (ref.get("voice_ratio") or 0) >= MIN_VOICE_RATIO
    key = "rate_total" if by_total else "rate_voice"
    unit_note = "含停顿" if by_total else "仅发声"
    a, b = narration.get(key), ref.get(key)
    if not a or not b:
        return 1.0, "没测出语速（参考段太短或没有识别结果），沿用原速"
    f = a / b
    # f > 1：旁白比面包快，要放慢 → 滑条往左；f < 1 反之。
    # 提示只说往哪边拉，不说重生成 —— 现在不重生成了。
    pct = abs(f - 1.0) * 100
    if abs(f - 1.0) < 0.05:
        return round(f, 3), (f"旁白 {a} vs 面包 {b} 字/秒（{unit_note}），"
                             f"已经很接近，保持当前语速即可")
    if f < lo or f > hi:
        direction = "往左（放慢）" if f > 1 else "往右（加快）"
        return min(max(f, lo), hi), (
            f"旁白 {a} vs 面包 {b} 字/秒（{unit_note}），差 {pct:.0f}%，"
            f"滑条{direction}；理论值 {f:.2f} 已按 {min(max(f, lo), hi):.2f} 计，再大会失真")
    direction = "往左（放慢）" if f > 1 else "往右（加快）"
    return round(f, 3), (f"旁白 {a} vs 面包 {b} 字/秒（{unit_note}），"
                         f"差 {pct:.0f}%，滑条{direction}")
