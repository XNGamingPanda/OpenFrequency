"""
Usage statistics — sends a privacy-preserving daily heartbeat to Cloudflare Workers.
Called once on app startup; silently no-ops if disabled or network is unavailable.
"""

import json
import locale
import os
import platform
import sys
import threading
from pathlib import Path

_WORKERS_URL    = 'https://robertwren.qzz.io'
_CLIENT_TOKEN   = 'oF9x-Km3p-Qr7n-Lv4w'
_STATE_FILE     = None   # set lazily from env


def _state_path() -> Path:
    global _STATE_FILE
    if _STATE_FILE is None:
        log_dir = Path(
            os.environ.get('OPENFREQUENCY_LOG_DIR')
            or Path.home() / 'AppData' / 'Roaming' / 'OpenFrequency' / 'logs'
        )
        _STATE_FILE = log_dir / 'stats_state.json'
    return _STATE_FILE


def _get_config() -> dict:
    path = os.environ.get('OPENFREQUENCY_CONFIG_PATH', 'config.json')
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return {}


def _get_version() -> str:
    for candidate in (
        Path(getattr(sys, '_MEIPASS', '')) / 'version.txt',
        Path(__file__).parent.parent / 'version.txt',
        Path('version.txt'),
    ):
        try:
            return candidate.read_text(encoding='utf-8').strip()
        except Exception:
            pass
    return 'unknown'


def _today_utc() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def _already_pinged_today() -> bool:
    try:
        raw = json.loads(_state_path().read_text(encoding='utf-8'))
        return raw.get('last_ping_date') == _today_utc()
    except Exception:
        return False


def _record_ping():
    try:
        sp = _state_path()
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(
            json.dumps({'last_ping_date': _today_utc()}, ensure_ascii=False),
            encoding='utf-8',
        )
    except Exception:
        pass


def _send_ping():
    """Do the actual HTTP POST — called in a daemon thread."""
    if _already_pinged_today():
        return

    cfg = _get_config()
    workers_url = (cfg.get('cloud', {}).get('workers_url') or _WORKERS_URL).rstrip('/')
    token = cfg.get('cloud', {}).get('client_token') or _CLIENT_TOKEN

    payload = {
        'app_version': _get_version(),
        'os': sys.platform.replace('win32', 'windows').replace('darwin', 'darwin'),
        'sim_type': cfg.get('simulator', {}).get('provider', 'unknown'),
        'locale': (locale.getdefaultlocale()[0] or 'unknown')[:16],
    }

    try:
        import requests
        resp = requests.post(
            f"{workers_url}/api/ping",
            json=payload,
            headers={'X-OF-Token': token},
            timeout=8,
        )
        if resp.status_code == 200:
            _record_ping()
    except Exception:
        pass  # Silently ignore — network unavailable, etc.


def ping_async():
    """Fire-and-forget: send heartbeat in background thread. Safe to call on startup."""
    t = threading.Thread(target=_send_ping, name='OF-StatsPing', daemon=True)
    t.start()
