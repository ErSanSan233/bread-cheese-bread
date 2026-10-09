"""启动前自检：把本机依赖状态打出来，缺必需项就以非零码退出。

    python server/preflight.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config  # noqa: E402

LABELS = {
    "ffmpeg": "ffmpeg",
    "tts_python": "IndexTTS2 解释器",
    "tts_ckpt": "IndexTTS2 权重",
    "whisper": "whisper 程序",
    "whisper_model": "whisper 权重",
}


def main() -> int:
    st = config.preflight()
    for k, label in LABELS.items():
        print(f"  {label:18} {'✓' if st[k] else '—'}")
    missing = st["missing"]
    print("缺失（必需）: " + ("、".join(missing) if missing else "无"))
    if missing:
        print("\n照着 bcb.env.example 补全 bcb.local.env 再跑。", file=sys.stderr)
        return 1
    if not (st["whisper"] and st["whisper_model"]):
        print("（whisper 没配齐，跳过回读质检，不影响生成）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
