"""
Auto-updater — checks Cloudflare Workers for latest release,
downloads installer, verifies SHA-256, launches installer.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_update_info: dict = None
_download_path: Path = None
_socketio = None

_CHECK_INTERVAL_SECONDS = 86400  # 24 hours

def _last_check_path() -> Path:
    return Path(os.environ.get('OPENFREQUENCY_CONFIG_PATH', 'config.json')).parent / '.update_last_check'

def _should_check() -> bool:
    """Return True if 24+ hours have passed since the last successful check."""
    p = _last_check_path()
    try:
        return (time.time() - p.stat().st_mtime) >= _CHECK_INTERVAL_SECONDS
    except Exception:
        return True

def _mark_checked():
    try:
        _last_check_path().touch()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------

def _get_config() -> dict:
    """Read config.json from the path specified by OPENFREQUENCY_CONFIG_PATH."""
    path = os.environ.get('OPENFREQUENCY_CONFIG_PATH', 'config.json')
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return {}


_DEFAULT_WORKERS_URL = 'https://robertwren.qzz.io'


def _get_workers_url() -> str:
    cfg = _get_config()
    return (cfg.get('cloud', {}).get('workers_url') or _DEFAULT_WORKERS_URL).rstrip('/')


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------

def _get_current_version() -> str:
    """Read current version from version.txt, fallback '0.0.0'."""
    for candidate in (
        Path(getattr(sys, '_MEIPASS', '')) / 'version.txt',
        Path(__file__).parent.parent / 'version.txt',
        Path('version.txt'),
    ):
        try:
            return candidate.read_text(encoding='utf-8').strip()
        except Exception:
            pass
    return '0.0.0'


def _version_tuple(v: str) -> tuple:
    """Convert a version string like 'v1.2.3' to (1, 2, 3)."""
    import re
    text = (v or '').strip().lstrip('vV')
    nums = [int(x) for x in re.findall(r'\d+', text)[:3]]
    while len(nums) < 3:
        nums.append(0)
    prerelease_rank = 0 if re.search(r'(alpha|beta|rc)', text, re.I) else 1
    return (*nums, prerelease_rank)


# ---------------------------------------------------------------------------
# SocketIO helper
# ---------------------------------------------------------------------------

def set_socketio(sio):
    """Store a SocketIO instance for emitting progress events."""
    global _socketio
    _socketio = sio


def _emit(event: str, data: dict):
    """Emit a SocketIO event if a socketio instance is available."""
    if _socketio is not None:
        try:
            _socketio.emit(event, data)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_update(socketio=None, silent: bool = True) -> dict | None:
    """
    Check Workers for a newer release.

    Returns the update_info dict if a newer version exists, else None.
    Emits SocketIO events: update_available | update_not_available | update_check_failed.
    """
    global _update_info

    if socketio is not None:
        set_socketio(socketio)

    # Skip background (silent) checks that ran less than 24 h ago
    if silent and not _should_check():
        print("Updater: skipping check — checked within last 24 h")
        return None

    try:
        workers_url = _get_workers_url()

        if not workers_url:
            raise ValueError('Workers URL not configured')

        resp = requests.get(
            f"{workers_url}/api/version",
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()

        latest = payload.get('latest', '0.0.0')
        current = _get_current_version()

        _mark_checked()

        if _version_tuple(latest) > _version_tuple(current):
            _update_info = payload
            _emit('update_available', {
                'latest': latest,
                'current': current,
                'release_notes_zh': payload.get('release_notes_zh', ''),
                'release_notes_en': payload.get('release_notes_en', ''),
                'assets': payload.get('assets', {}),
                'force_update': payload.get('force_update', False),
                'tag': payload.get('tag', latest),
            })
            return _update_info
        else:
            _update_info = None
            if not silent:
                _emit('update_not_available', {'current': current, 'latest': latest})
            return None

    except Exception as exc:
        _emit('update_check_failed', {'error': str(exc)})
        return None


def download_update(asset_key: str = 'win_x64', socketio=None) -> Path | None:
    """
    Download the installer for the given asset key with resume support.

    Emits: update_download_progress | update_download_complete | update_download_failed.
    Returns Path to the downloaded file, or None on failure.
    """
    global _download_path

    if socketio is not None:
        set_socketio(socketio)

    if not _update_info:
        _emit('update_download_failed', {'reason': 'No update info available — call check_update() first'})
        return None

    try:
        assets = _update_info.get('assets', {})
        if asset_key not in assets:
            _emit('update_download_failed', {'reason': f"Asset '{asset_key}' not found in update info"})
            return None

        asset = assets[asset_key]
        dl_path = asset.get('dl_path', '')
        if not dl_path:
            _emit('update_download_failed', {'reason': 'Asset has no dl_path'})
            return None

        workers_url = _get_workers_url()
        # dl_path from Workers may already be a full URL (contains ://) or a path-only string
        if dl_path.startswith('http://') or dl_path.startswith('https://'):
            url = dl_path
        else:
            url = f"{workers_url}{dl_path}"

        filename = Path(url.split('?')[0]).name or 'of_update_installer.exe'
        dest_dir = Path(tempfile.gettempdir()) / 'of_update'
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / filename

        # Resume support: check existing partial download
        downloaded = 0
        if dest.exists():
            downloaded = dest.stat().st_size

        headers = {}
        if downloaded > 0:
            headers['Range'] = f'bytes={downloaded}-'

        resp = requests.get(url, stream=True, headers=headers, timeout=60)

        # Server may not support range requests → start fresh
        if resp.status_code == 200 and downloaded > 0:
            downloaded = 0
        elif resp.status_code == 206:
            pass  # resume accepted
        elif resp.status_code not in (200, 206):
            resp.raise_for_status()

        total_header = resp.headers.get('Content-Length')
        if total_header:
            total = int(total_header) + downloaded
        else:
            total = asset.get('size', 0)

        mode = 'ab' if downloaded > 0 and resp.status_code == 206 else 'wb'
        chunk_size = 65536  # 64 KB

        t_start = time.monotonic()
        last_emit = t_start

        with open(dest, mode) as fh:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if chunk:
                    fh.write(chunk)
                    downloaded += len(chunk)

                    now = time.monotonic()
                    elapsed = now - t_start or 0.001
                    speed_kbps = (downloaded / elapsed) / 1024
                    percent = round(downloaded / total * 100, 1) if total else 0
                    eta = round((total - downloaded) / (downloaded / elapsed)) if downloaded and total > downloaded else 0

                    # Throttle SocketIO events to ~10 Hz
                    if now - last_emit >= 0.1:
                        _emit('update_download_progress', {
                            'downloaded': downloaded,
                            'total': total,
                            'percent': percent,
                            'speed_kbps': round(speed_kbps, 1),
                            'eta_seconds': eta,
                        })
                        last_emit = now

        # SHA-256 verification
        expected_sha = asset.get('sha256', '')
        if expected_sha:
            if not verify_sha256(dest, expected_sha):
                _emit('update_download_failed', {'reason': 'SHA-256 mismatch — file may be corrupted'})
                try:
                    dest.unlink(missing_ok=True)
                except Exception:
                    pass
                return None

        _download_path = dest
        _emit('update_download_complete', {'path': str(dest), 'filename': filename})
        return dest

    except Exception as exc:
        _emit('update_download_failed', {'reason': str(exc)})
        return None


def verify_sha256(path: Path, expected: str) -> bool:
    """Verify SHA-256 of a file in 1 MB chunks. Returns True if match."""
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as fh:
            while True:
                block = fh.read(1024 * 1024)
                if not block:
                    break
                h.update(block)
        return h.hexdigest().lower() == expected.lower()
    except Exception:
        return False


def launch_installer(path: Path = None) -> bool:
    """
    Launch the downloaded installer silently.

    Uses _download_path if path is not provided.
    Returns True if the process was started successfully.
    """
    global _download_path
    target = path or _download_path
    if target is None:
        raise ValueError('No installer path available')

    if sys.platform == 'win32':
        subprocess.Popen(
            [str(target), '/SILENT'],
            shell=False,
            close_fds=True,
        )
    else:
        # macOS / Linux: attempt direct execution
        subprocess.Popen(
            [str(target)],
            shell=False,
            close_fds=True,
        )
    return True


def get_update_info() -> dict | None:
    """Return the last fetched update info dict, or None."""
    return _update_info
