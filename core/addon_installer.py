"""
addon_installer.py — Download and install simulator add-ons from the DLC catalog.

Currently supports MSFS Community folder installs (FBW A32NX, etc.).
Progress is reported via a callback: callback(phase, pct, message)
  phase: 'download' | 'extract' | 'done' | 'error'
  pct:   0-100
  message: human-readable status string
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Callable, Optional

import requests


# ── DLC catalog loader ────────────────────────────────────────────────────────

def load_dlc_catalog() -> list[dict]:
    """Load the bundled DLC catalog from data/dlc_catalog.json."""
    catalog_path = os.path.join(
        os.path.dirname(__file__), '..', 'data', 'dlc_catalog.json'
    )
    try:
        with open(catalog_path, encoding='utf-8') as f:
            return json.load(f).get('items', [])
    except Exception as e:
        print(f"AddonInstaller: Cannot load DLC catalog — {e}")
        return []


def get_dlc_item(dlc_id: str) -> Optional[dict]:
    return next((d for d in load_dlc_catalog() if d['id'] == dlc_id), None)


# ── Progress tracking ─────────────────────────────────────────────────────────

class InstallProgress:
    """Thread-safe progress container; can also be polled via /api/dlc/progress."""

    def __init__(self):
        self._lock  = threading.Lock()
        self.phase   = 'idle'    # idle | download | extract | done | error
        self.pct     = 0
        self.message = ''
        self.dlc_id  = ''

    def update(self, phase: str, pct: int, message: str):
        with self._lock:
            self.phase   = phase
            self.pct     = pct
            self.message = message

    def snapshot(self) -> dict:
        with self._lock:
            return {
                'dlc_id':  self.dlc_id,
                'phase':   self.phase,
                'pct':     self.pct,
                'message': self.message,
            }


# Global singleton — only one install at a time
_progress = InstallProgress()


def current_progress() -> dict:
    return _progress.snapshot()


# ── Core installer ────────────────────────────────────────────────────────────

class AddonInstaller:
    """
    Downloads and installs DLC items from the catalog into the simulator's
    Community folder (or plugin community folder for OF plugins).
    """

    def __init__(self, config: dict):
        self.config = config

    # ── Public install entry-point ────────────────────────────────────────────

    def install_async(self, dlc_id: str, socketio=None) -> threading.Thread:
        """
        Start an async install in a background thread.
        Progress is pushed via socketio event 'dlc_progress' if provided.
        """
        t = threading.Thread(
            target=self._install,
            args=(dlc_id, socketio),
            daemon=True,
        )
        t.start()
        return t

    def _install(self, dlc_id: str, socketio=None):
        def emit(phase, pct, msg):
            _progress.update(phase, pct, msg)
            if socketio:
                socketio.emit('dlc_progress', _progress.snapshot())

        _progress.dlc_id = dlc_id

        item = get_dlc_item(dlc_id)
        if not item:
            emit('error', 0, f"DLC '{dlc_id}' not found in catalog.")
            return

        # Resolve install destination
        dest_root = self._resolve_install_root(item)
        if not dest_root:
            emit('error', 0,
                 "Install path not configured. Please set the MSFS Community folder "
                 "in Settings → Simulator.")
            return

        install_subdir = item.get('install_subdir', dlc_id)
        dest_dir = os.path.join(dest_root, install_subdir)

        url = item['download_url']
        emit('download', 0, f"Downloading {item['name']} …")

        try:
            with tempfile.TemporaryDirectory() as tmp:
                zip_path = os.path.join(tmp, f"{dlc_id}.zip")
                self._download(url, zip_path, lambda p: emit('download', p,
                    f"Downloading {item['name']} … {p}%"))

                emit('extract', 0, f"Extracting to {dest_dir} …")
                self._extract(zip_path, dest_dir, lambda p: emit('extract', p,
                    f"Extracting … {p}%"))

        except Exception as e:
            emit('error', 0, str(e))
            return

        emit('done', 100, f"{item['name']} installed successfully.")

    # ── Download helper ───────────────────────────────────────────────────────

    @staticmethod
    def _download(url: str, dest_path: str, progress_cb: Callable[[int], None]):
        """Stream-download url → dest_path, calling progress_cb(0-100) as it goes."""
        resp = requests.get(url, stream=True, timeout=60,
                            headers={'User-Agent': 'OpenFrequency-Installer/1.0'})
        resp.raise_for_status()

        total = int(resp.headers.get('Content-Length', 0))
        downloaded = 0
        chunk_size = 1 << 17   # 128 KB

        with open(dest_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    progress_cb(min(99, int(downloaded / total * 100)))

        progress_cb(100)

    # ── Extract helper ────────────────────────────────────────────────────────

    @staticmethod
    def _extract(zip_path: str, dest_dir: str, progress_cb: Callable[[int], None]):
        """
        Extract ZIP into dest_dir.
        If the ZIP contains a single top-level folder, its contents are
        extracted directly into dest_dir (avoids A32NX/A32NX/ nesting).
        """
        with zipfile.ZipFile(zip_path, 'r') as zf:
            members = zf.namelist()
            # Detect single top-level folder
            top_dirs = {m.split('/')[0] for m in members if '/' in m}
            single_root = (len(top_dirs) == 1
                           and all(m.startswith(next(iter(top_dirs))) for m in members))
            strip_prefix = (next(iter(top_dirs)) + '/') if single_root else ''

            # Remove existing installation
            if os.path.isdir(dest_dir):
                shutil.rmtree(dest_dir)
            os.makedirs(dest_dir, exist_ok=True)

            total = len(members)
            for i, member in enumerate(members):
                rel = member[len(strip_prefix):] if strip_prefix else member
                if not rel:
                    continue
                target = os.path.join(dest_dir, rel)
                if member.endswith('/'):
                    os.makedirs(target, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(member) as src, open(target, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                if total:
                    progress_cb(min(99, int((i + 1) / total * 100)))

        progress_cb(100)

    # ── Path resolution ───────────────────────────────────────────────────────

    def _resolve_install_root(self, item: dict) -> Optional[str]:
        """
        Find the install root directory from config based on item['config_key'].
        E.g. config_key='msfs_community' → config['simulator']['msfs_community']
        """
        config_key = item.get('config_key', '')
        sim_cfg = self.config.get('simulator', {})
        path = sim_cfg.get(config_key, '')
        if path and os.path.isdir(path):
            return path
        return None


# ── Singleton ─────────────────────────────────────────────────────────────────
_installer: Optional[AddonInstaller] = None


def get_installer(config: dict) -> AddonInstaller:
    global _installer
    if _installer is None:
        _installer = AddonInstaller(config)
    _installer.config = config
    return _installer
