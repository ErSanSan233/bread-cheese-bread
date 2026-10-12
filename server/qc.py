"""质检：用本机 whisper large-v3-mlx 回读，确认合成出来的东西和稿子一致。

两个用途：
1. 识别参考片段内容 —— 用户框完 AC，先告诉他这段到底在说什么、语气对不对。
2. 回读旁白 —— 合成完再转一遍，跟输入稿逐字比对，别靠耳朵感觉。
"""
import difflib
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path

from . import config


class QCError(RuntimeError):
    pass


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[\s，。、！？；：,.!?;:\"'“”‘’…—\-~·]+", "", s)
    return s


# 公开别名：语速统计要跟这里用同一套字数口径，
# 否则「稿子字数」和「ASR 字数」两边数出来的数不可比。
normalize = _norm


def transcribe(wav: Path, language: str = "zh",
               initial_prompt: str | None = None,
               timeout: int = 900) -> str:
    if not Path(config.MLX_WHISPER).exists():
        raise QCError(f"找不到 mlx_whisper：{config.MLX_WHISPER}")
    tmp = Path(tempfile.mkdtemp(prefix="bcb_asr_"))
    try:
        cmd = [
            config.MLX_WHISPER, str(wav),
            "--model", config.WHISPER_MODEL,
            "--output-dir", str(tmp),
            "--output-format", "json",
            "--language", language,
        ]
        if initial_prompt:
            cmd += ["--initial-prompt", initial_prompt]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise QCError(f"whisper 失败：{(r.stderr or r.stdout)[-600:]}")
        files = list(tmp.glob("*.json"))
        if not files:
            raise QCError("whisper 没有产出结果")
        data = json.loads(files[0].read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return str(data.get("text") or "").strip()
        if isinstance(data, list):
            return "".join(str(x.get("text") or "") for x in data).strip()
        return str(data)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def compare(expect: str, got: str) -> dict:
    a, b = _norm(expect), _norm(got)
    ratio = difflib.SequenceMatcher(None, a, b).ratio() if (a or b) else 1.0
    sm = difflib.SequenceMatcher(None, a, b)
    diffs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        diffs.append({
            "type": tag,
            "expect": a[i1:i2],
            "got": b[j1:j2],
        })
    return {
        "similarity": round(ratio, 4),
        "expect_len": len(a),
        "got_len": len(b),
        "diffs": diffs[:12],
        "diff_count": len(diffs),
    }
