"""环境常量。

所有外部工具的绝对路径都走环境变量，仓库里不硬编码任何本机路径
（`bcb.local.env` 不入库，见 `bcb.env.example`）。
ffmpeg / mlx_whisper 若在 PATH 里能找到就自动用，不用配。
"""
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
WEB = ROOT / "web"


def _tool(cmd: str, env_key: str) -> str | None:
    return os.environ.get(env_key) or shutil.which(cmd)


# ---- 命令行工具 ----
FFMPEG = _tool("ffmpeg", "BCB_FFMPEG")
FFPROBE = _tool("ffprobe", "BCB_FFPROBE")

# ---- IndexTTS2（重模型，跑在它自己的解释器里）----
TTS_PY = os.environ.get("BCB_TTS_PY")            # 必须：IndexTTS2 所在 venv 的 python
TTS_CWD = os.environ.get("BCB_TTS_CWD")          # 建议：indextts 源码目录（可选）
TTS_CKPT = os.environ.get("BCB_TTS_CKPT")        # 必须：checkpoints 目录

# ---- 回读质检（可选）----
MLX_WHISPER = _tool("mlx_whisper", "BCB_MLX_WHISPER")
WHISPER_MODEL = os.environ.get("BCB_WHISPER_MODEL")

# ---- 音频常量 ----
REF_RATE = 32000          # 给模型用的参考音轨（单声道）
WAVE_PEAKS = 2400         # 波形峰值点数
TARGET_LUFS = -16.0       # 可选「整体响度统一」目标
TARGET_TP = -1.5
TARGET_LRA = 11.0

# 自动精选参考窗口的候选长度（秒），按优先级
REF_WINDOW_CANDIDATES = (12.0, 10.0, 14.0, 8.0)
REF_WINDOW_MAX = 15.0     # 超过这个长度才启动自动精选
REF_WINDOW_MIN = 3.0      # 短于此认为信息量不足


def ensure_dirs() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "jobs").mkdir(parents=True, exist_ok=True)


def preflight() -> dict:
    """启动前自检：缺什么就报什么，别等到点了生成才炸。"""
    missing: list[str] = []
    if not FFMPEG:
        missing.append("ffmpeg（BCB_FFMPEG 或放进 PATH）")
    if not TTS_PY or not Path(TTS_PY).exists():
        missing.append("IndexTTS2 的 python 解释器（BCB_TTS_PY）")
    if not TTS_CKPT or not Path(TTS_CKPT).exists():
        missing.append("IndexTTS2 权重目录（BCB_TTS_CKPT）")
    return {
        "ffmpeg": bool(FFMPEG),
        "tts_python": bool(TTS_PY) and Path(TTS_PY).exists(),
        "tts_ckpt": bool(TTS_CKPT) and Path(TTS_CKPT).exists(),
        "whisper": bool(MLX_WHISPER) and Path(MLX_WHISPER).exists(),
        "whisper_model": bool(WHISPER_MODEL) and Path(WHISPER_MODEL).exists(),
        "missing": missing,
    }
