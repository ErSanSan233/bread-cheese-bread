"""IndexTTS2 常驻进程的客户端。

模型进程跑在它自己的 venv（config.TTS_PY）里，本项目环境完全不装 torch。
首次调用会拉起进程并加载模型（约 1~2 分钟），之后复用。
"""
import json
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config

_lock = threading.Lock()
_proc = None
_port: int | None = None
_logfh = None


def _drain(fh):
    try:
        for _ in fh:
            pass
    except Exception:
        pass


def _spawn() -> int:
    global _proc, _port, _logfh
    config.ensure_dirs()
    if not config.TTS_PY:
        raise RuntimeError(
            "没配 BCB_TTS_PY —— 在 bcb.local.env 里指向 IndexTTS2 所在 venv 的 python")
    worker = config.ROOT / "worker" / "tts_server.py"
    _logfh = open(config.WORK / "tts_server.log", "ab", buffering=0)
    # 日志是追加写、跨轮累积的，每次拉起打一行分隔，回头翻才知道哪段是哪一轮
    _logfh.write(
        f"\n===== worker 启动 {time.strftime('%Y-%m-%d %H:%M:%S')} "
        f"(后端 pid {os.getpid()}) =====\n".encode("utf-8"))
    _logfh.flush()
    # macOS 的 libmalloc 会在每次新建进程时往 stderr 打一行
    # "MallocStackLogging: can't turn off ..."。env 里带 Malloc* 变量时会触发，
    # 这里连同子进程一并清掉，还终端一个干净的输出。
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("Malloc") and not k.startswith("MALLOC")}
    if config.TTS_CKPT:
        env["BCB_TTS_CKPT"] = config.TTS_CKPT
    _proc = subprocess.Popen(
        [config.TTS_PY, "-u", str(worker)],
        cwd=config.TTS_CWD or None,
        stdout=subprocess.PIPE,
        stderr=_logfh,
        text=True,
        bufsize=1,
        env=env,
    )
    line = _proc.stdout.readline()
    if not line or not line.startswith("BCBPORT:"):
        tail = ""
        try:
            tail = (config.WORK / "tts_server.log").read_text(
                encoding="utf-8", errors="ignore")[-1500:]
        except Exception:
            pass
        raise RuntimeError(f"模型进程启动失败。日志尾部：\n{tail}")
    _port = int(line.strip().split(":")[1])
    threading.Thread(target=_drain, args=(_proc.stdout,), daemon=True).start()
    return _port


def ensure_worker() -> int:
    global _proc
    with _lock:
        if _proc is not None and _proc.poll() is None and _port:
            return _port
        return _spawn()


def reap_stale() -> int:
    """清掉上一次跑残留的模型进程，返回杀掉的个数。

    后端被强杀时（Ctrl-C 按第二次、kill -9、终端窗口被关），@tts_server.py
    会变成孤儿继续占着十几 GB。下一次 ./run.sh 又会新拉一个，两个叠着加载，
    机器会卡到连播放器都动不了。启动前先把上一轮的清干净。
    """
    worker = config.ROOT / "worker" / "tts_server.py"
    try:
        r = subprocess.run(["pgrep", "-f", str(worker)],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return 0
    me = os.getpid()
    killed = 0
    for tok in r.stdout.split():
        if not tok.isdigit():
            continue
        pid = int(tok)
        if pid == me:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            killed += 1
        except Exception:
            pass
    return killed


def warmup() -> None:
    """后台预加载模型，避免用户第一次点生成干等。"""
    def _go():
        try:
            port = ensure_worker()
            _req("GET", f"http://127.0.0.1:{port}/load", timeout=1800)
        except Exception as e:
            print(f"[warmup] 预加载失败：{e}", flush=True)
    threading.Thread(target=_go, daemon=True).start()


def _req(method: str, url: str, payload: dict | None = None,
         timeout: float = 1800) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def synth(voice: str | Path, text: str, out: str | Path,
          speed: float = 1.0, emo_vector: list[float] | None = None,
          timeout: float = 1800) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"voice": str(voice), "text": text, "out": str(out), "speed": speed}
    if emo_vector:
        payload["emo_vector"] = emo_vector

    for attempt in (0, 1):
        port = ensure_worker()
        try:
            r = _req("POST", f"http://127.0.0.1:{port}/synth", payload, timeout=timeout)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == 0:
                shutdown()
                continue
            raise RuntimeError(f"合成请求失败：{e}")
        if not r.get("ok"):
            raise RuntimeError(r.get("error") or "合成失败")
        if not out.exists():
            raise RuntimeError("模型没有产出音频文件")
        return out
    raise RuntimeError("合成失败")


def shutdown() -> None:
    global _proc, _port, _logfh
    with _lock:
        if _proc is not None:
            try:
                _proc.terminate()
                _proc.wait(timeout=10)
            except Exception:
                try:
                    _proc.kill()
                except Exception:
                    pass
        _proc, _port, _logfh = None, None, None
