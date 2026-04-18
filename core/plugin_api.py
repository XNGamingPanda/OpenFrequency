"""
plugin_api.py — Public API exposed to community plugins.

Every plugin must:
  1. Live in  plugins/community/<plugin_id>/
  2. Contain  manifest.json  (see schema below)
  3. Contain  the entry-point file declared in manifest["entry"]
  4. Define a class named  Plugin  that subclasses  OpenFrequencyPlugin

Manifest schema
---------------
{
  "id":              "my_plugin",          // unique snake_case identifier
  "name":            "My Plugin",          // display name
  "version":         "1.0.0",
  "author":          "Author Name",
  "description":     "Short description",
  "entry":           "plugin.py",          // relative to plugin folder
  "min_app_version": "1.0.0",             // optional minimum OF version
  "hooks": [                              // list of hooks the plugin uses
    "on_atc_response",
    "on_telemetry",
    "on_frequency_change"
  ],
  "settings_schema": {},                   // optional plugin settings JSON-Schema
  "cabin_media": [                         // optional cabin media definitions
    {
      "id":        "my_boarding",          // unique id (snake_case)
      "name":      "My Airline Boarding",  // English display name
      "name_zh":   "我的航空登机音乐",     // optional Chinese name
      "file":      "media/boarding.mp3",   // path relative to plugin folder
      "callsigns": ["MYA", "MY"],          // ICAO/IATA prefixes to auto-match; [] = all
      "trigger":   "boarding",            // boarding|deboarding|safety|custom
      "loop":      false
    }
  ]
}

Available hooks (override in your Plugin subclass)
---------------------------------------------------
  on_load()                              → called once after plugin loads
  on_unload()                            → called before plugin is disabled/removed
  on_atc_response(text, action) → str    → may return modified text or None (no change)
  on_telemetry(data: dict)               → telemetry snapshot every ~100 ms
  on_frequency_change(frequency: float)  → COM1 frequency changed
  on_pilot_input(text: str)              → pilot speech/text before LLM
  on_chat_message(sender, text)          → any chat message logged
  on_cabin_media_play(media_id)          → a cabin media entry was triggered

Plugin API (available as self.<method>)
---------------------------------------
  self.emit(event, data)           → emit a socket.io event to all dashboards
  self.subscribe(event, callback)  → subscribe to an internal EventBus event
  self.config                      → plugin's own config dict (persisted)
  self.save_config()               → write plugin config back to disk
  self.log(msg)                    → log with plugin prefix
  self.shared_context              → read-only snapshot of shared_context
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional


class OpenFrequencyPlugin:
    """
    Base class for all OpenFrequency community plugins.
    Subclass this and name your class ``Plugin``.
    """

    # Filled in by PluginManager when the plugin is loaded
    _manager   = None   # reference back to PluginManager
    _socketio  = None   # Flask-SocketIO instance
    _event_bus = None   # core.context.event_bus
    _ctx_lock  = None   # core.context.context_lock
    _shared_ctx = None  # core.context.shared_context (live dict, use with lock)
    _plugin_dir = None  # absolute path to this plugin's folder

    # ── Plugin metadata (set by PluginManager from manifest) ─────────────────
    plugin_id   : str = "unknown"
    plugin_name : str = "Unknown Plugin"
    version     : str = "0.0.0"
    author      : str = ""
    description : str = ""

    def __init__(self):
        self._config: dict = {}
        self._load_config()

    # ── Lifecycle hooks (override in subclass) ────────────────────────────────

    def on_load(self):
        """Called once when the plugin is enabled / first loaded."""

    def on_unload(self):
        """Called before the plugin is disabled or removed."""

    # ── Event hooks (override whichever you need) ─────────────────────────────

    def on_atc_response(self, text: str, action: Optional[str]) -> Optional[str]:
        """
        Called with every ATC response before it is broadcast.
        Return a modified string to replace the text, or None to leave unchanged.
        """
        return None

    def on_telemetry(self, data: dict):
        """Called with the latest telemetry snapshot (~10 Hz)."""

    def on_frequency_change(self, frequency: float):
        """Called when the pilot tunes a new COM1 frequency."""

    def on_pilot_input(self, text: str) -> Optional[str]:
        """
        Called with pilot speech/text before the LLM processes it.
        Return modified text or None to leave unchanged.
        """
        return None

    def on_chat_message(self, sender: str, text: str):
        """Called for every message logged in the Comms Log."""

    def on_cabin_media_play(self, media_id: str):
        """Called when a cabin media entry is triggered (by UI or automation)."""

    # ── Plugin API (use in your hook methods) ─────────────────────────────────

    def emit(self, event: str, data: Any = None):
        """Emit a socket.io event to all connected dashboards."""
        if self._socketio:
            self._socketio.emit(event, data)

    def subscribe(self, event: str, callback):
        """Subscribe to an internal EventBus event."""
        if self._event_bus:
            self._event_bus.on(event, callback)

    def log(self, msg: str):
        """Log a message prefixed with the plugin name."""
        print(f"[Plugin:{self.plugin_name}] {msg}")

    @property
    def config(self) -> dict:
        return self._config

    @property
    def shared_context(self) -> dict:
        """Return a snapshot of shared_context (does NOT hold the lock)."""
        import copy
        if self._ctx_lock and self._shared_ctx:
            with self._ctx_lock:
                return copy.deepcopy(self._shared_ctx)
        return {}

    def save_config(self):
        """Persist plugin config to <plugin_dir>/config.json."""
        if not self._plugin_dir:
            return
        cfg_path = os.path.join(self._plugin_dir, 'config.json')
        try:
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.log(f"Failed to save config: {e}")

    # ── Private helpers ───────────────────────────────────────────────────────

    def _load_config(self):
        if not self._plugin_dir:
            return
        cfg_path = os.path.join(self._plugin_dir, 'config.json')
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, encoding='utf-8') as f:
                    self._config = json.load(f)
            except Exception:
                self._config = {}
