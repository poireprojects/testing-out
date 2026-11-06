"""A tiny stand-in for Ollama's HTTP API, backed by llama.cpp — for machines where Ollama itself can't be
installed (e.g. a CI/cloud test box). The app talks to it exactly as it talks to Ollama.

    python scripts/ollama_shim.py --model path/to/Llama-3.2-3B-Instruct-Q4_K_M.gguf --name llama3.2:3b

Implements only what the app uses: GET /api/tags and POST /api/chat (with a JSON-schema "format").
Not part of the app; on a normal computer install Ollama from the guided setup instead.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--name", default="llama3.2:3b")
    ap.add_argument("--port", type=int, default=11434)
    ap.add_argument("--threads", type=int, default=4)
    a = ap.parse_args()
    from llama_cpp import Llama
    llm = Llama(model_path=a.model, n_ctx=4096, n_threads=a.threads, verbose=False, seed=11)
    lock = threading.Lock()

    class H(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/api/tags"):
                return self._send(200, {"models": [{"name": a.name, "model": a.name}]})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self.path.startswith("/api/chat"):
                return self._send(404, {"error": "not found"})
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            fmt = req.get("format")
            rf = {"type": "json_object", "schema": fmt} if isinstance(fmt, dict) else ({"type": "json_object"} if fmt == "json" else None)
            opts = req.get("options") or {}
            t0 = time.monotonic()
            try:
                with lock:
                    r = llm.create_chat_completion(messages=req.get("messages", []), response_format=rf,
                                                   temperature=float(opts.get("temperature", 0.1)),
                                                   max_tokens=int(opts.get("num_predict", 800)))
            except Exception as exc:
                return self._send(500, {"error": str(exc)})
            u = r.get("usage", {})
            self._send(200, {"model": a.name, "message": {"role": "assistant", "content": r["choices"][0]["message"]["content"]},
                             "done": True, "prompt_eval_count": u.get("prompt_tokens", 0), "eval_count": u.get("completion_tokens", 0),
                             "total_duration": int((time.monotonic() - t0) * 1e9)})

        def log_message(self, *args):
            pass

    print(f"ollama-compatible shim for {a.name} on :{a.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
