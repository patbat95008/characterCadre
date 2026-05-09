"""
LLM debug logger for CharacterCadre.

When CC_DEBUG=1 is set, all Ollama calls are logged to two files in CC_LOGS_DIR:
  ollama-input.log  — prompts sent to Ollama, formatted as numbered role blocks
  ollama-output.log — responses received, formatted as plain text or key:value pairs

Both files are truncated at session start. Neither file is created in normal mode.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

_ENABLED = os.environ.get("CC_DEBUG") == "1"
_SEP_HEAVY = "═" * 72
_SEP_LIGHT = "─" * 72

_lock = threading.Lock()
_input_file: IO[str] | None = None
_output_file: IO[str] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _open_logs() -> None:
    global _input_file, _output_file
    logs_dir = Path(os.environ.get("CC_LOGS_DIR", "logs"))
    logs_dir.mkdir(parents=True, exist_ok=True)

    header = f"\n{_SEP_HEAVY}\nSESSION STARTED {_now()}\n{_SEP_HEAVY}\n\n"

    _input_file = open(logs_dir / "ollama-input.log", "w", encoding="utf-8")
    _input_file.write(header)
    _input_file.flush()

    _output_file = open(logs_dir / "ollama-output.log", "w", encoding="utf-8")
    _output_file.write(header)
    _output_file.flush()


if _ENABLED:
    _open_logs()


def _entry_header(call_type: str, model: str) -> str:
    label = f"─── {call_type} · {model} · {_now()} "
    return label + "─" * max(0, 72 - len(label)) + "\n"


def _format_value(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return str(v).lower()
    return str(v)


def log_input(call_type: str, model: str, messages: list[dict[str, str]]) -> None:
    if not _ENABLED or _input_file is None:
        return
    lines = [_entry_header(call_type, model)]
    for i, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        lines.append(f"[{i}] {role}\n")
        for line in content.splitlines():
            lines.append(f"  {line}\n")
        lines.append("\n")
    lines.append(_SEP_LIGHT + "\n\n")
    with _lock:
        _input_file.write("".join(lines))
        _input_file.flush()


def log_output(call_type: str, model: str, response: str | dict[str, Any]) -> None:
    if not _ENABLED or _output_file is None:
        return
    lines = [_entry_header(call_type, model), "\n"]
    if isinstance(response, dict):
        if response:
            max_key_len = max(len(k) for k in response)
            for k, v in response.items():
                lines.append(f"  {k:<{max_key_len}} : {_format_value(v)}\n")
        lines.append("\n")
    else:
        for line in response.splitlines():
            lines.append(f"  {line}\n")
        lines.append("\n")
    lines.append(_SEP_LIGHT + "\n\n")
    with _lock:
        _output_file.write("".join(lines))
        _output_file.flush()
