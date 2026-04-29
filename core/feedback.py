"""
Manual feedback — collects user text + optional log/config digest,
sanitises PII, POSTs to Cloudflare Workers.
"""

import json
import os
import sys
from copy import deepcopy
from pathlib import Path

import requests

from core.telemetry import _SANITIZE_PATTERNS, _get_app_version


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


# ---------------------------------------------------------------------------
# Sanitisation
# ---------------------------------------------------------------------------

# Windows full path pattern: keep only the last filename component
_WIN_PATH_RE = None
try:
    import re
    _WIN_PATH_RE = re.compile(r'(?i)[A-Z]:\\(?:[^\\]+\\)*([^\\]+)')
except Exception:
    pass


def sanitize_log(text: str) -> str:
    """
    Apply PII-scrubbing patterns from telemetry.

    Windows full paths are reduced to their final filename component.
    """
    # Replace full Windows paths, keeping only the filename
    if _WIN_PATH_RE is not None:
        text = _WIN_PATH_RE.sub(r'...\\\1', text)

    # Apply shared sanitisation patterns (API keys, tokens, emails, home dirs …)
    for pattern, replacement in _SANITIZE_PATTERNS:
        text = pattern.sub(replacement, text)

    return text


# ---------------------------------------------------------------------------
# Data collectors
# ---------------------------------------------------------------------------

def collect_log_excerpt(lines: int = None) -> str:
    """
    Read all lines from the most-recent .log file and sanitise.

    Returns sanitised text, or an empty string on any error.
    """
    try:
        log_dir_env = os.environ.get('OPENFREQUENCY_LOG_DIR')
        if log_dir_env:
            log_dir = Path(log_dir_env)
        else:
            log_dir = Path.home() / 'AppData' / 'Roaming' / 'OpenFrequency' / 'logs'

        log_files = sorted(log_dir.glob('*.log'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not log_files:
            return ''

        latest = log_files[0]
        raw_text = latest.read_text(encoding='utf-8', errors='replace')
        return sanitize_log(raw_text)
    except Exception:
        return ''


def collect_config_summary() -> str:
    """
    Return a sanitised JSON string of the config.

    Returns an empty string on any error.
    """
    try:
        cfg = _get_config()
        sanitized = _sanitize_config(cfg)
        return json.dumps(sanitized, ensure_ascii=False)
    except Exception:
        return ''


def _sanitize_config(cfg: dict) -> dict:
    """Deep-copy config and zero out sensitive fields at any nesting level."""
    SENSITIVE_NAMES = {'password', 'token', 'secret', 'key', 'api_key', 'base_url'}

    def _scrub(obj):
        if isinstance(obj, dict):
            result = {}
            for k, v in obj.items():
                k_lower = k.lower()
                if k_lower in SENSITIVE_NAMES:
                    result[k] = '***'
                elif k_lower == 'base_url' and isinstance(v, str) and any(
                    tok in v for tok in ('sk-', 'AIza', 'Bearer', 'token', 'key')
                ):
                    result[k] = '***'
                else:
                    result[k] = _scrub(v)
            return result
        if isinstance(obj, list):
            return [_scrub(item) for item in obj]
        return obj

    return _scrub(deepcopy(cfg))


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------

def submit_feedback(
    type_: str,
    title: str,
    description: str,
    *,
    crash_id: str = None,
    contact: str = None,
    include_log: bool = False,
    include_config: bool = False,
    app_version: str = None,
) -> tuple[bool, str]:
    """
    Submit user feedback to the Workers endpoint.

    Parameters
    ----------
    type_ : str
        Feedback category, e.g. 'bug', 'feature', 'other'.
    title : str
        Short summary of the feedback.
    description : str
        Full user-provided description.
    crash_id : str, optional
        Associated crash ID from telemetry, if any.
    contact : str, optional
        User contact info (email/handle). Will be sanitised before sending.
    include_log : bool
        If True, attach recent log excerpt.
    include_config : bool
        If True, attach sanitised config summary.
    app_version : str, optional
        Override app version string. Auto-detected if None.

    Returns
    -------
    (True, "#TICKET_ID") on success, (False, "error message") on failure.
    """
    _DEFAULT_WORKERS_URL = 'https://robertwren.qzz.io'
    cfg = _get_config()
    workers_url = (cfg.get('cloud', {}).get('workers_url') or _DEFAULT_WORKERS_URL).rstrip('/')
    token = cfg.get('cloud', {}).get('client_token') or os.environ.get('OPENFREQUENCY_CLIENT_TOKEN', '')
    if not token:
        return False, 'Cloud client token is not configured.'

    payload: dict = {
        'type': type_,
        'title': title[:256],
        'description': description[:4096],
        'os_platform': sys.platform,
        'app_version': app_version or _get_app_version(),
    }

    if crash_id:
        payload['crash_id'] = crash_id

    if contact:
        # Sanitise contact field to strip accidental secrets
        from core.telemetry import _sanitize_str
        payload['contact'] = _sanitize_str(contact)[:128]

    if include_log:
        payload['log_excerpt'] = collect_log_excerpt()

    if include_config:
        payload['config_summary'] = collect_config_summary()

    try:
        resp = requests.post(
            f"{workers_url}/api/feedback",
            json=payload,
            headers={'X-OF-Token': token},
            timeout=20,
        )
        if resp.status_code == 201:
            body = resp.json() if resp.content else {}
            ticket_id = body.get('id', '')
            return True, f"#{ticket_id}" if ticket_id else (True, 'submitted')
        else:
            try:
                msg = resp.json().get('error', resp.text[:256])
            except Exception:
                msg = resp.text[:256] or f"HTTP {resp.status_code}"
            return False, msg
    except requests.Timeout:
        return False, 'Request timed out (20 s)'
    except requests.ConnectionError as exc:
        return False, f"Connection error: {exc}"
    except Exception as exc:
        return False, str(exc)
