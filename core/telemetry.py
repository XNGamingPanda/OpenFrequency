"""
Crash telemetry — captures unhandled exceptions, writes local logs,
and optionally uploads to Cloudflare Workers.
"""

import hashlib
import json
import locale
import os
import platform
import re
import sys
import threading
import time
import traceback
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_START_TIME = time.time()

# Compiled sanitisation patterns (applied in _sanitize_str)
_SANITIZE_PATTERNS = [
    (re.compile(r'sk-[A-Za-z0-9]{10,}'), 'sk-***'),
    (re.compile(r'AIza[A-Za-z0-9_\-]{30,}'), 'AIza***'),
    (re.compile(r'Bearer [A-Za-z0-9._\-]{10,}'), 'Bearer ***'),
    (re.compile(r'(?i)(C:\\Users\\)[^\\]+'), r'\1***'),
    (re.compile(r'/home/[^/]+'), '/home/***'),
    (re.compile(r'[\w.+\-]+@[\w\-]+\.[a-z]{2,}'), '***@***.***'),
]

# Singleton reference
_manager = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_config() -> dict:
    """Read config.json from the path specified by OPENFREQUENCY_CONFIG_PATH."""
    path = os.environ.get('OPENFREQUENCY_CONFIG_PATH', 'config.json')
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return {}


def _get_app_version() -> str:
    """Read application version from version.txt or __version__, fallback 'unknown'."""
    # Try version.txt next to the executable / working dir
    for candidate in (
        Path(getattr(sys, '_MEIPASS', '')) / 'version.txt',
        Path(__file__).parent.parent / 'version.txt',
        Path('version.txt'),
    ):
        try:
            return candidate.read_text(encoding='utf-8').strip()
        except Exception:
            pass
    # Try importing package __version__
    try:
        import openfrequency  # type: ignore
        return openfrequency.__version__
    except Exception:
        pass
    return 'unknown'


def _sanitize_str(text: str) -> str:
    """Apply regex substitutions to remove PII and secrets from a string."""
    for pattern, replacement in _SANITIZE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _sanitize_config(cfg: dict) -> dict:
    """Deep-copy config and zero out sensitive fields at any nesting level."""
    SENSITIVE_NAMES = {'password', 'token', 'secret', 'key', 'api_key', 'base_url'}

    def _scrub(obj, parent_key: str = ''):
        if isinstance(obj, dict):
            result = {}
            for k, v in obj.items():
                k_lower = k.lower()
                # Zero out by name
                if k_lower in SENSITIVE_NAMES:
                    result[k] = '***'
                # Also zero connection.base_url only if it looks like it contains a key
                elif k_lower == 'base_url' and isinstance(v, str) and any(
                    tok in v for tok in ('sk-', 'AIza', 'Bearer', 'token', 'key')
                ):
                    result[k] = '***'
                else:
                    result[k] = _scrub(v, k)
            return result
        if isinstance(obj, list):
            return [_scrub(item) for item in obj]
        return obj

    return _scrub(deepcopy(cfg))


# ---------------------------------------------------------------------------
# TelemetryManager
# ---------------------------------------------------------------------------

class TelemetryManager:
    """Singleton that manages crash telemetry for OpenFrequency."""

    def __init__(self):
        log_dir = Path(
            os.environ.get('OPENFREQUENCY_LOG_DIR')
            or Path.home() / 'AppData' / 'Roaming' / 'OpenFrequency' / 'logs'
        )
        self._crash_dir: Path = log_dir / 'crashes'
        self._state_path: Path = log_dir / 'telemetry_state.json'
        self._uploaded: set = set()
        self._lock = threading.Lock()
        self._original_excepthook = sys.excepthook
        self._hooks_installed = False

        # Ensure directories exist
        self._crash_dir.mkdir(parents=True, exist_ok=True)

        self._load_state()

    # ------------------------------------------------------------------
    # Hook installation
    # ------------------------------------------------------------------

    def install_hooks(self):
        """Register sys.excepthook and threading.excepthook (idempotent)."""
        if self._hooks_installed:
            return
        self._original_excepthook = sys.excepthook
        sys.excepthook = self._excepthook
        threading.excepthook = lambda args: self._excepthook(
            args.exc_type, args.exc_value, args.exc_traceback
        )
        self._hooks_installed = True

    def _excepthook(self, exc_type, exc_value, exc_tb):
        """Global exception handler — skip KeyboardInterrupt, then report."""
        if exc_type is KeyboardInterrupt:
            self._original_excepthook(exc_type, exc_value, exc_tb)
            return
        try:
            self.report(exc_type, exc_value, exc_tb)
        except Exception:
            pass
        self._original_excepthook(exc_type, exc_value, exc_tb)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def report(self, exc_type, exc_value, exc_tb, *, user_note: str = None) -> str:
        """Capture crash data, write locally, optionally upload. Returns crash_id."""
        data = self._collect(exc_type, exc_value, exc_tb)
        crash_id = data['crash_id']

        if user_note:
            data['user_note'] = _sanitize_str(user_note)

        # Write local JSON
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        filename = f"{timestamp}_{crash_id[:8]}.json"
        try:
            crash_file = self._crash_dir / filename
            crash_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
        except Exception:
            pass

        # Upload in background if enabled
        if self.is_enabled():
            t = threading.Thread(
                target=self.upload_crash,
                args=(crash_id, data, user_note),
                daemon=True,
            )
            t.start()

        return crash_id

    def _collect(self, exc_type, exc_value, exc_tb) -> dict:
        """Gather crash context into a dict."""
        cfg = _get_config()
        include_sim = cfg.get('cloud', {}).get('include_sim_info', False)

        tb_text = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))

        data = {
            'crash_id': str(uuid.uuid4()),
            'app_version': _get_app_version(),
            'os_platform': sys.platform,
            'os_version': platform.version(),
            'python_version': platform.python_version(),
            'exception_type': exc_type.__name__ if exc_type else 'Unknown',
            'exception_message': _sanitize_str(str(exc_value)),
            'traceback': _sanitize_str(tb_text),
            'timestamp_utc': datetime.now(timezone.utc).isoformat(),
            'uptime_seconds': round(time.time() - _START_TIME, 1),
        }

        if include_sim:
            try:
                data['sim_provider'] = cfg.get('simulator', {}).get('provider', '')
            except Exception:
                data['sim_provider'] = ''
            try:
                data['llm_provider'] = cfg.get('connection', {}).get('provider', '')
            except Exception:
                data['llm_provider'] = ''
            try:
                data['locale'] = locale.getdefaultlocale()[0]
            except Exception:
                data['locale'] = ''
            try:
                sanitized = _sanitize_config(cfg)
                cfg_json = json.dumps(sanitized, sort_keys=True, ensure_ascii=False)
                data['config_hash'] = hashlib.sha256(cfg_json.encode()).hexdigest()
            except Exception:
                data['config_hash'] = ''

        return data

    def upload_crash(self, crash_id: str, data: dict, user_note: str = None) -> bool:
        """POST crash data to Workers endpoint. Returns True on success."""
        with self._lock:
            if crash_id in self._uploaded:
                return True

        _DEFAULT_WORKERS_URL = 'https://robertwren.qzz.io'
        cfg = _get_config()
        workers_url = (cfg.get('cloud', {}).get('workers_url') or _DEFAULT_WORKERS_URL).rstrip('/')

        payload = dict(data)
        if user_note is not None:
            payload['user_note'] = _sanitize_str(user_note)

        try:
            resp = requests.post(
                f"{workers_url}/api/crash",
                json=payload,
                timeout=10,
            )
            if resp.status_code in (201, 409):
                with self._lock:
                    self._uploaded.add(crash_id)
                self._persist_state()
                return True
            return False
        except Exception:
            return False

    def get_recent_crashes(self, n: int = 5) -> list:
        """Return up to n most-recent crash dicts, sorted by mtime descending."""
        try:
            files = sorted(
                self._crash_dir.glob('*.json'),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            results = []
            for f in files[:n]:
                try:
                    results.append(json.loads(f.read_text(encoding='utf-8')))
                except Exception:
                    pass
            return results
        except Exception:
            return []

    def is_enabled(self) -> bool:
        """Return True if telemetry is enabled in config (default: False)."""
        cfg = _get_config()
        return bool(cfg.get('cloud', {}).get('telemetry_enabled', False))

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _persist_state(self):
        """Write the set of uploaded crash IDs to disk."""
        try:
            self._state_path.write_text(
                json.dumps({'uploaded': list(self._uploaded)}, ensure_ascii=False),
                encoding='utf-8',
            )
        except Exception:
            pass

    def _load_state(self):
        """Load previously uploaded crash IDs from disk."""
        try:
            raw = json.loads(self._state_path.read_text(encoding='utf-8'))
            self._uploaded = set(raw.get('uploaded', []))
        except Exception:
            self._uploaded = set()


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def get_manager() -> TelemetryManager:
    """Return the singleton TelemetryManager, creating it on first call."""
    global _manager
    if _manager is None:
        _manager = TelemetryManager()
    return _manager


def install_hooks():
    """Install global exception hooks via the singleton manager."""
    get_manager().install_hooks()
