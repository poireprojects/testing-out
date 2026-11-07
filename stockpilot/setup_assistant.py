"""One-click local AI setup: detect, install and start Ollama, then download a model.

Everything the README used to ask you to do in a terminal happens here, with progress
reported back to the setup wizard.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import httpx

log = logging.getLogger(__name__)
Progress = Callable[[float, str], None]

OLLAMA_DOWNLOADS = {
    "win32": "https://ollama.com/download/OllamaSetup.exe",
    "darwin": "https://ollama.com/download/Ollama-darwin.zip",
}
OLLAMA_PAGE = "https://ollama.com/download"
ALPACA_SIGNUP = "https://app.alpaca.markets/signup"
ALPACA_KEYS_PAGE = "https://app.alpaca.markets/paper/dashboard/overview"
ANTHROPIC_KEYS_PAGE = "https://console.anthropic.com/settings/keys"


def total_memory_gb() -> float:
    try:
        if sys.platform == "win32":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))  # type: ignore[attr-defined]
            return m.ullTotalPhys / 1024 ** 3
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout
            return int(out.strip()) / 1024 ** 3
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
    except Exception:
        return 8.0


def recommended_model(mem_gb: Optional[float] = None) -> tuple[str, str]:
    mem = total_memory_gb() if mem_gb is None else mem_gb
    if mem >= 15:
        return "llama3.1:8b", "about 4.9 GB download — good quality, needs 16 GB of memory"
    return "llama3.2:3b", "about 2.0 GB download — lighter model for computers with less memory"


def _ollama_binary() -> Optional[str]:
    found = shutil.which("ollama")
    if found:
        return found
    candidates = []
    if sys.platform == "win32":
        candidates.append(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe")
    elif sys.platform == "darwin":
        candidates += [Path("/Applications/Ollama.app/Contents/Resources/ollama"),
                       Path.home() / "Applications/Ollama.app/Contents/Resources/ollama", Path("/usr/local/bin/ollama")]
    else:
        candidates += [Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")]
    return next((str(c) for c in candidates if c.exists()), None)


@dataclass
class OllamaStatus:
    installed: bool
    running: bool
    models: list[str]
    can_auto_install: bool

    def has(self, model: str) -> bool:
        return model in self.models or f"{model}:latest" in self.models


def _http(url: str, client: Optional[httpx.Client]) -> httpx.Client:
    return client or httpx.Client(timeout=10, trust_env=False)


def status(base_url: str = "http://localhost:11434", client: Optional[httpx.Client] = None) -> OllamaStatus:
    running, models = False, []
    try:
        r = _http(base_url, client).get(f"{base_url.rstrip('/')}/api/tags", timeout=3)
        r.raise_for_status()
        running = True
        models = [m.get("name", "") for m in r.json().get("models", [])]
    except (httpx.HTTPError, ValueError):
        pass
    return OllamaStatus(installed=running or _ollama_binary() is not None, running=running, models=models,
                        can_auto_install=sys.platform in OLLAMA_DOWNLOADS)


def install(progress: Progress, client: Optional[httpx.Client] = None) -> str:
    """Download the official Ollama installer and start it. Returns a short status message."""
    url = OLLAMA_DOWNLOADS.get(sys.platform)
    if url is None:
        raise RuntimeError(f"automatic install isn't available on this system — get it from {OLLAMA_PAGE}")
    tmp = Path(tempfile.mkdtemp(prefix="stockpilot-ollama-"))
    dest = tmp / url.rsplit("/", 1)[-1]
    c = client or httpx.Client(timeout=60, follow_redirects=True)
    with c.stream("GET", url, follow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with dest.open("wb") as fh:
            for chunk in r.iter_bytes(1 << 16):
                fh.write(chunk)
                done += len(chunk)
                if total:
                    progress(done / total * 0.9, f"downloading Ollama… {done / 1e6:.0f} of {total / 1e6:.0f} MB")
    if sys.platform == "win32":
        progress(0.95, "opening the Ollama installer — click through it, then come back here")
        os.startfile(str(dest))  # type: ignore[attr-defined]
        return "the Ollama installer is open. finish it, then click “check again”."
    target = Path.home() / "Applications"
    target.mkdir(exist_ok=True)
    with zipfile.ZipFile(dest) as z:
        z.extractall(target)
    progress(0.97, "starting Ollama")
    subprocess.Popen(["open", str(target / "Ollama.app")])
    return "Ollama is installed in your Applications folder and starting up."


def start() -> bool:
    """Start Ollama if it's installed but not running."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-a", "Ollama"])
            return True
        if sys.platform == "win32":
            app = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama app.exe"
            if app.exists():
                os.startfile(str(app))  # type: ignore[attr-defined]
                return True
        b = _ollama_binary()
        if b:
            subprocess.Popen([b, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    except Exception as exc:
        log.warning("could not start Ollama: %s", exc)
    return False


def wait_until_running(base_url: str, seconds: float = 30, client: Optional[httpx.Client] = None,
                       sleep: Callable[[float], None] = time.sleep) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if status(base_url, client).running:
            return True
        sleep(1.0)
    return False


def pull_model(base_url: str, model: str, progress: Progress, client: Optional[httpx.Client] = None) -> None:
    """Download a model through Ollama's own API, streaming progress."""
    c = client or httpx.Client(timeout=None, trust_env=False)
    with c.stream("POST", f"{base_url.rstrip('/')}/api/pull", json={"model": model, "stream": True}) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("error"):
                raise RuntimeError(f"model download failed: {ev['error']}")
            total, done = ev.get("total"), ev.get("completed")
            if total and done is not None:
                progress(done / total, f"downloading {model}… {done / 1e9:.1f} of {total / 1e9:.1f} GB")
            else:
                progress(-1.0, ev.get("status", "working…"))
            if ev.get("status") == "success":
                progress(1.0, f"{model} is ready")
                return
    raise RuntimeError("model download ended unexpectedly — try again")
