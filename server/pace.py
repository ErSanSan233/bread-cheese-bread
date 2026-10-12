"""语速对齐：让旁白的语速贴近两片面包。

为什么需要这一步：界面上那个「语速」值其实是 IndexTTS2 的 duration_factor，
用户没法知道该给多少才能跟上原片的节奏。这里把面包的实际语速测出来，
用第一遍合成的结果反推系数，再合成第二遍。

    factor = 旁白语速（duration_factor=1 的首合成） / 面包语速

factor > 1 表示旁白说得比面包快，要放慢 —— duration_factor 越大越慢。
因为 IndexTTS2 里 `target_lengths = codes * 1.72 * duration_factor` 是线性的，
一次就能算准，不需要来回迭代。

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

- 只对齐平均语速。句内的起伏、哪个词拖长，那一层是 GPT 决定的，这里动不了。
- 抽样本身有随机性（temperature 0.8），第二遍的 code 数会浮动，实际残差约 3~8%。
- 太大太小都会失真，钳在 [0.7, 1.6]。
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
    if abs(f - 1.0) < 0.05:
        return round(f, 3), (f"旁白 {a} vs 面包 {b} 字/秒（{unit_note}），"
                             f"已经很接近，不重生成")
    if f < lo:
        return lo, (f"旁白比面包慢很多（{a} vs {b} 字/秒，{unit_note}），"
                    f"理论值 {f:.2f}，先按 {lo} 处理避免失真")
    if f > hi:
        return hi, (f"旁白比面包快很多（{a} vs {b} 字/秒，{unit_note}），"
                    f"理论值 {f:.2f}，先按 {hi} 处理避免失真")
    return round(f, 3), (f"旁白 {a} vs 面包 {b} 字/秒（{unit_note}），"
                         f"按 {f:.2f} 倍时长重生成")
