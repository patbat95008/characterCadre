"""
CharacterCadre unified launcher.

Starts Ollama, the FastAPI backend, and the Vite frontend in a single
terminal window. Press Ctrl+C (or close this window) to stop everything.
"""
import atexit
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV_PYTHON = str(BACKEND / ".venv" / "Scripts" / "python.exe")
OLLAMA_MODEL = "mistral-small3.1"
OLLAMA_URL = "http://localhost:11434"

# ANSI colours — work in Windows Terminal and modern cmd
_C = {
    "ollama":   "\033[96m",
    "backend":  "\033[92m",
    "frontend": "\033[93m",
    "info":     "\033[97m",
    "warn":     "\033[91m",
    "reset":    "\033[0m",
}

_procs: list[subprocess.Popen] = []


def _tag(label: str, text: str) -> str:
    return f"{_C[label]}[{label}]{_C['reset']} {text}"


def _stream(proc: subprocess.Popen, label: str) -> None:
    assert proc.stdout
    for raw in iter(proc.stdout.readline, b""):
        print(_tag(label, raw.decode(errors="replace").rstrip()), flush=True)


def _launch(cmd: str, cwd: Path | None, label: str, env: dict | None = None) -> subprocess.Popen:
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=True,
    )
    _procs.append(proc)
    threading.Thread(target=_stream, args=(proc, label), daemon=True).start()
    return proc


def _ollama_listening() -> bool:
    try:
        with socket.create_connection(("localhost", 11434), timeout=1):
            return True
    except OSError:
        return False


def _warm_up_model() -> None:
    """Wait for Ollama to accept connections, then load the model into memory."""
    for _ in range(30):
        if _ollama_listening():
            break
        time.sleep(2)
    else:
        print(_tag("warn", "Ollama did not start in time — model warm-up skipped."), flush=True)
        return

    payload = f'{{"model":"{OLLAMA_MODEL}","keep_alive":-1}}'.encode()
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            resp.read()
        print(_tag("ollama", f"Model {OLLAMA_MODEL} loaded and ready."), flush=True)
    except Exception as exc:
        print(_tag("warn", f"Model warm-up failed: {exc}"), flush=True)


def _shutdown() -> None:
    print(f"\n{_C['info']}Shutting down…{_C['reset']}", flush=True)
    for proc in _procs:
        try:
            proc.terminate()
        except Exception:
            pass
    for proc in _procs:
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def main() -> None:
    # Enable ANSI on older Windows consoles
    if sys.platform == "win32":
        os.system("")

    atexit.register(_shutdown)

    debug_mode = "--debug" in sys.argv

    print(f"{_C['info']}CharacterCadre — starting services…{_C['reset']}\n", flush=True)

    # ── Ollama ──────────────────────────────────────────────────────────────
    if _ollama_listening():
        print(_tag("ollama", "Server already running."), flush=True)
    else:
        print(_tag("ollama", "Starting server…"), flush=True)
        _launch("ollama serve", cwd=None, label="ollama")

    threading.Thread(target=_warm_up_model, daemon=True).start()

    # ── Backend ─────────────────────────────────────────────────────────────
    print(_tag("backend", "Starting uvicorn…"), flush=True)
    backend_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if debug_mode:
        logs_dir = ROOT / "logs"
        logs_dir.mkdir(exist_ok=True)
        backend_env["CC_DEBUG"] = "1"
        backend_env["CC_LOGS_DIR"] = str(logs_dir)
        print(_tag("info", f"Debug mode — LLM logs → {logs_dir}"), flush=True)
    _launch(
        f'"{VENV_PYTHON}" -u -m uvicorn app.main:app --reload --port 8000 --log-level info',
        cwd=BACKEND,
        label="backend",
        env=backend_env,
    )

    # ── Frontend ────────────────────────────────────────────────────────────
    print(_tag("frontend", "Starting Vite…"), flush=True)
    _launch("npm run dev", cwd=FRONTEND, label="frontend")

    print(f"\n{_C['info']}All services started.{_C['reset']}")
    print(f"  Ollama:   {OLLAMA_URL}")
    print(f"  Backend:  http://localhost:8000")
    print(f"  Frontend: http://localhost:5173")
    print(f"\n{_C['info']}Press Ctrl+C to stop everything.{_C['reset']}\n", flush=True)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
