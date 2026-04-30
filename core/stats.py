"""
Usage statistics — sends a privacy-preserving daily heartbeat to Cloudflare Workers.
Called once on app startup; silently no-ops if disabled or network is unavailable.
"""

import json
import locale
import os
import sys
import threading
import uuid
from pathlib import Path

_WORKERS_URL    = 'https://robertwren.qzz.io'
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


def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_state(state: dict):
    try:
        sp = _state_path()
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    except Exception:
        pass


def _already_pinged_today() -> bool:
    return _load_state().get('last_ping_date') == _today_utc()


def _get_or_create_device_id() -> str:
    """
    Return a stable random UUID for this installation.
    Generated once, stored in stats_state.json, never changes.
    This lets Workers deduplicate by device rather than IP address,
    making DAU accurate even when users switch VPN nodes.
    """
    state = _load_state()
    did = state.get('device_id')
    if not did:
        did = str(uuid.uuid4())
        state['device_id'] = did
        _save_state(state)
    return did


def _record_ping():
    try:
        state = _load_state()
        state['last_ping_date'] = _today_utc()
        _save_state(state)
    except Exception:
        pass


def _get_sim_type(cfg: dict) -> str:
    """
    Derive the active simulator type.
    Prefer the shared_context value (set when sim actually connects)
    so that users who connected during this session are counted correctly.
    Falls back to the config provider setting.
    """
    # Try live context first (set by SimBridge once connected)
    try:
        from core.context import shared_context
        sim_type = shared_context.get('sim_type') or shared_context.get('simulator_type')
        if sim_type and sim_type != 'unknown':
            return sim_type.lower()
    except Exception:
        pass
    # Fall back to configured provider
    return cfg.get('simulator', {}).get('provider', 'unknown')


def _send_ping():
    """Do the actual HTTP POST — called in a daemon thread."""
    if _already_pinged_today():
        return

    cfg = _get_config()
    workers_url = (cfg.get('cloud', {}).get('workers_url') or _WORKERS_URL).rstrip('/')

    payload = {
        'app_version': _get_version(),
        'os': sys.platform.replace('win32', 'windows').replace('darwin', 'darwin'),
        'sim_type': _get_sim_type(cfg),
        'locale': (locale.getdefaultlocale()[0] or 'unknown')[:16],
        'device_id': _get_or_create_device_id(),
    }

    try:
        import requests
        resp = requests.post(
            f"{workers_url}/api/ping",
            json=payload,
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
