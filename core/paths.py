"""
Centralised path helpers for OpenFrequency.

Read-only bundle assets  → cwd / sys._MEIPASS  (handled by os.chdir in launcher)
Writable runtime data    → OPENFREQUENCY_RUNTIME_DIR  (set by desktop_launcher)
                           Falls back to cwd in development.
"""
from __future__ import annotations

import os
from pathlib import Path


def _runtime_root() -> Path:
    """Return the writable runtime directory (APPDATA\\OpenFrequency in packaged mode)."""
    env = os.environ.get("OPENFREQUENCY_RUNTIME_DIR", "")
    return Path(env) if env else Path.cwd()


def writable_data_path(*parts: str) -> str:
    """Return an absolute path under the writable data directory and create it."""
    p = _runtime_root() / "data" / Path(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def writable_path(*parts: str) -> str:
    """Return an absolute path under the writable runtime directory (no mkdir)."""
    return str(_runtime_root() / Path(*parts))
