"""常驻 IndexTTS2 服务 —— 跑在 IndexTTS2 自己的 venv 里（不是项目的 conda 环境）。

为什么常驻：模型加载一次要一分多钟，用户调稿子会反复重合成，
每次重启进程 = 每次多等一分多钟。

为什么走 HTTP 而不是 stdin/stdout 协议：indextts / transformers 会往 stdout 打日志，
用 stdout 传 JSON 会被污染。HTTP 只依赖标准库，不需要给重模型环境装任何东西。

启动后往 stdout 打一行 `BCBPORT:<port>`，父进程据此连上来。
"""
import json
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 让 BCB_TTS_CWD 真正对 import 生效。
# 以 `python -u xxx.py` 启动时 sys.path[0] 是脚本所在目录，不含 cwd；
# IndexTTS2 若非 editable 安装，或源码目录被人挪过位置（editable 的 .pth 里
# 写的是绝对路径，挪完就失效），少了这几行会直接 ModuleNotFoundError。
_cwd = os.getcwd()
if _cwd not in sys.path:
    sys.path.insert(0, _cwd)

CKPT = os.environ.get("BCB_TTS_CKPT") or str(
    Path(__file__).resolve().parents[1] / "checkpoints"
)

_model = None
_model_lock = threading.Lock()
_infer_lock = threading.Lock()


def get_model():
    global _model
    with _model_lock:
        if _model is None:
            sys.stderr.write("[tts_server] 正在加载模型…\n")
            sys.stderr.flush()
            from indextts.infer_v2_5 import IndexTTS2

            _model = IndexTTS2(
                cfg_path=str(Path(CKPT) / "config.yaml"),
                model_dir=str(CKPT),
                use_deepspeed=False,
                use_cuda_kernel=False,
            )
            sys.stderr.write("[tts_server] 模型就绪\n")
            sys.stderr.flush()
        return _model


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # 别把请求日志打进 stdout

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/ping":
            self._json({"ok": True, "loaded": _model is not None})
        elif self.path == "/load":
            try:
                get_model()
                self._json({"ok": True, "loaded": True})
            except Exception as e:
                traceback.print_exc()
                self._json({"ok": False, "error": str(e)}, 500)
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/synth":
            self._json({"ok": False, "error": "not found"}, 404)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
            out = req["out"]
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            tts = get_model()
            kwargs = {}
            if req.get("emo_vector"):
                kwargs["emo_vector"] = req["emo_vector"]
            if req.get("emo_alpha") is not None:
                kwargs["emo_alpha"] = float(req["emo_alpha"])
            with _infer_lock:  # MPS 上推理不能并发
                tts.infer(
                    spk_audio_prompt=req["voice"],
                    text=req["text"],
                    output_path=out,
                    lang="ZH",
                    verbose=False,
                    duration_factor=float(req.get("speed") or 1.0),
                    **kwargs,
                )
            ok = Path(out).exists()
            self._json({"ok": ok, "out": out,
                        "error": None if ok else "模型没有产出文件"})
        except Exception as e:
            traceback.print_exc()
            self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    print(f"BCBPORT:{port}", flush=True)
    sys.stderr.write(f"[tts_server] listening on {port}\n")
    sys.stderr.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
