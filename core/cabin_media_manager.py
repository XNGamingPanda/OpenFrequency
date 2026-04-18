"""
cabin_media_manager.py — Merged cabin media registry.

Media sources (merged in priority order, later overrides earlier):
  1. Built-in  : data/cabin_media/manifest.json  (shipped with app)
  2. Plugin    : each plugin's manifest.json "cabin_media" array
  3. User      : %APPDATA%\OpenFrequency\cabin_media\manifest.json  (custom)
               or  <exe_dir>/cabin_media/manifest.json  (portable packaged)

Manifest entry schema
---------------------
{
  "id":        "ana_boarding",           // unique identifier
  "name":      "ANA Boarding Music",     // display name (English)
  "name_zh":   "全日空登机音乐",          // optional zh display name
  "file":      "media/boarding.mp3",     // path relative to manifest's directory
  "callsigns": ["ANA", "NH"],            // airline ICAO/IATA prefix list (case-insensitive)
  "trigger":   "boarding",              // "boarding"|"deboarding"|"safety"|"custom"
  "loop":      false                    // whether to loop the audio
}

When callsigns is empty or omitted the entry is shown for all flights.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Optional


class CabinMediaManager:
    """Singleton that merges cabin media from built-in, plugin, and user sources."""

    def __init__(self):
        self._lock = threading.Lock()
        # { id: entry_dict }  (merged, last-writer-wins on id collision)
        self._registry: dict[str, dict] = {}
        self._current_callsign: str = ''
        self._socketio = None

    # ── Wiring ────────────────────────────────────────────────────────────────

    def attach_socketio(self, socketio):
        self._socketio = socketio

    def set_callsign(self, callsign: str):
        """Update the active flight callsign and notify the dashboard."""
        cs = (callsign or '').upper().strip()
        if cs == self._current_callsign:
            return
        self._current_callsign = cs
        self._notify()

    # ── Registry management ───────────────────────────────────────────────────

    def load_builtin(self):
        """Load from data/cabin_media/manifest.json (shipped with app)."""
        candidates = [
            Path(sys.executable).parent / 'data' / 'cabin_media' / 'manifest.json',
            Path(__file__).parent.parent / 'data' / 'cabin_media' / 'manifest.json',
        ]
        for p in candidates:
            if p.exists():
                self._load_manifest(p)
                return

    def load_user(self):
        """Load from %APPDATA%\OpenFrequency\cabin_media\manifest.json or portable path."""
        candidates: list[Path] = []
        appdata = os.environ.get('APPDATA')
        if appdata:
            candidates.append(Path(appdata) / 'OpenFrequency' / 'cabin_media' / 'manifest.json')
        # Portable (next to exe)
        candidates.append(Path(sys.executable).parent / 'cabin_media' / 'manifest.json')
        for p in candidates:
            if p.exists():
                self._load_manifest(p)

    def register_plugin_media(self, plugin_dir: str, entries: list[dict]):
        """Called by PluginManager for each plugin that declares cabin_media."""
        plugin_path = Path(plugin_dir)
        with self._lock:
            for raw in entries:
                entry = dict(raw)
                entry['_source'] = 'plugin'
                # Resolve file path relative to plugin directory
                if entry.get('file'):
                    abs_file = plugin_path / entry['file']
                    entry['_abs_file'] = str(abs_file)
                eid = entry.get('id')
                if eid:
                    self._registry[eid] = entry
        self._notify()

    def _load_manifest(self, manifest_path: Path):
        try:
            with open(manifest_path, encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            print(f"CabinMediaManager: failed to load {manifest_path}: {e}")
            return
        base_dir = manifest_path.parent
        entries = data if isinstance(data, list) else data.get('entries', [])
        with self._lock:
            for raw in entries:
                entry = dict(raw)
                if entry.get('file'):
                    abs_file = base_dir / entry['file']
                    entry['_abs_file'] = str(abs_file)
                entry.setdefault('_source', 'builtin')
                eid = entry.get('id')
                if eid:
                    self._registry[eid] = entry

    # ── Query ─────────────────────────────────────────────────────────────────

    def all_media(self) -> list[dict]:
        """All registered media entries (full registry)."""
        with self._lock:
            return list(self._registry.values())

    def media_for_callsign(self, callsign: str | None = None) -> list[dict]:
        """
        Return media entries that match *callsign* (or current callsign if None).
        An entry with an empty/missing callsigns list is shown for all flights.
        """
        cs = (callsign or self._current_callsign).upper().strip()
        result = []
        with self._lock:
            for entry in self._registry.values():
                patterns = entry.get('callsigns') or []
                if not patterns:
                    result.append(entry)
                    continue
                for pat in patterns:
                    if cs.startswith(pat.upper()):
                        result.append(entry)
                        break
        return result

    # ── Notification ──────────────────────────────────────────────────────────

    def _notify(self):
        """Push updated cabin media list to all dashboards."""
        if not self._socketio:
            return
        items = self.media_for_callsign()
        safe = [self._safe_entry(e) for e in items]
        self._socketio.emit('cabin_media_updated', {'media': safe})

    @staticmethod
    def _safe_entry(entry: dict) -> dict:
        """Strip internal keys before sending to frontend."""
        return {k: v for k, v in entry.items() if not k.startswith('_')}

    # ── Play ──────────────────────────────────────────────────────────────────

    def play(self, media_id: str):
        """
        Emit a play event for *media_id* to all connected dashboards.
        The frontend handles actual <audio>/<video> playback.
        """
        with self._lock:
            entry = self._registry.get(media_id)
        if not entry:
            print(f"CabinMediaManager: unknown media id '{media_id}'")
            return
        if self._socketio:
            self._socketio.emit('cabin_media_play', self._safe_entry(entry))


# Singleton
cabin_media_manager = CabinMediaManager()
