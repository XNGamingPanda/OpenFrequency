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
    "on_frequency_change",
    "on_sim_connected",
    "on_flight_phase_change"
  ],
  "settings_schema": {},                   // optional plugin settings JSON-Schema
  "cabin_scripts": {                      // optional cabin script definitions (follows data/cabin/scripts.json format)
    "MYA": {                              // airline code key (e.g., CCA, UAL, ANA)
      "voice": "en-US-JennyNeural",       // TTS voice
      "welcome": "Welcome aboard...",      // simple string or object with "text" field
      "door_close": "Flight attendants, doors to arrival and cross check.",
      "safety_demo": {                    // can be object with text and optional video
        "text": "Please watch the safety demonstration.",
        "video": "media/safety.mp4"       // optional video file path
      },
      "takeoff_prep": "Cabin crew, seats for takeoff.",
      "climb_service": "We will now begin our inflight service.",
      "descent": "We are beginning our descent.",
      "landing_prep": "Cabin crew, seats for landing.",
      "arrival_prep": "Please remain seated until the aircraft has come to a complete stop.",
      "turbulence": "Due to turbulence, please return to your seats.",
      "deboarding": "Thank you for flying with us."
    }
  }
}

Available hooks (override in your Plugin subclass)
---------------------------------------------------
  ── Lifecycle ──
  on_load()                                    → called once after plugin loads
  on_unload()                                  → called before plugin is disabled/removed
  on_app_start()                               → app fully initialized, sim not yet connected
  on_app_shutdown()                            → graceful shutdown in progress

  ── Simulator ──
  on_sim_connected(sim_type: str)              → SimConnect / X-Plane connected
  on_sim_disconnected()                        → simulator disconnected
  on_flight_phase_change(phase: str)           → 'ground'|'taxi'|'climb'|'cruise'|'descent'|'approach'|'landed'
  on_altitude_crossing(altitude: float,        → every time aircraft crosses a round-thousand ft level
                        direction: str)           direction: 'ascending'|'descending'
  on_gear_change(position: str)               → 'up'|'down'|'transit'
  on_park_brake(engaged: bool)                → parking brake engaged/released
  on_engine_state(running: list)              → list of running engine indices (0-based)
  on_autopilot_change(engaged: bool,          → AP master engaged/disengaged + mode string
                       mode: str)
  on_lights_change(lights: dict)              → {'beacon','strobe','landing','nav','taxi'} bool values
  on_squawk_change(squawk: str)               → transponder code changed (e.g. '7700')
  on_fuel_change(fuel_total: float,           → total fuel quantity changed
                   fuel_each: list)             → fuel per tank changed

  ── Audio ──
  on_tts_speak(text: str)                     → TTS about to speak (read-only, cannot modify)
  on_stt_result(text: str)                    → STT recognized pilot speech
  on_audio_playback_start(text: str)          → audio playback started
  on_audio_playback_end(text: str)            → audio playback ended

  ── Communications ──
  on_atc_response(text, action) → str         → may return modified text or None (no change)
  on_pilot_input(text: str) → str             → pilot speech/text before LLM; return modified or None
  on_chat_message(sender, text)               → any chat message logged
  on_atis_ready(icao: str, text: str)         → ATIS generated for an airport
  on_atc_action(action: str, params: dict)    → ATC issued an instruction (hold, descend, contact, etc.)
  on_radio_message(freq: float, text: str)    → any radio message on a frequency

  ── Environment & Weather ──
  on_weather_update(icao: str, weather: dict) → weather data updated for an airport
  on_traffic_update(icao: str, traffic: list) → traffic data updated for an airport
  on_time_of_day_change(hour: int)            → simulation time changed (0-23)

  ── Navigation & Route ──
  on_waypoint_reached(waypoint: str)          → aircraft reached a waypoint
  on_route_change(route: str)                 → flight route changed
  on_sid_change(sid: str)                      → SID changed
  on_star_change(star: str)                    → STAR changed
  on_approach_change(approach: str)           → approach procedure changed

  ── Config ──
  on_config_change(config: dict)              → user saved settings; full config dict provided
  on_plugin_config_change(plugin_config: dict) → plugin's own config changed

  ── Other ──
  on_frequency_change(frequency: float)       → COM1 frequency changed
  on_telemetry(data: dict)                    → telemetry snapshot every ~100 ms
  on_cabin_media_play(media_id)               → a cabin media entry was triggered
  on_custom_event(event_name: str, data: any) → custom event triggered by another plugin

Plugin API (available as self.<method>)
---------------------------------------
  self.emit(event, data)               → emit a socket.io event to all dashboards
  self.subscribe(event, callback)      → subscribe to an internal EventBus event
  self.speak(text, priority=1)         → trigger TTS (priority 1=ATC, 2=alert, 3=background)
  self.notify(title, body, level='info')  → send a dashboard toast notification
  self.schedule(interval_sec, fn)      → call fn() repeatedly; returns a cancel handle
  self.inject_atc_message(text)        → push a synthetic ATC message into the comms log
  self.get_config_value(key_path, default=None)  → read app config via dot notation
  self.set_context_value(section, key, value)    → write a value into shared_context (thread-safe)
  self.get_context_value(section, key, default=None) → read a value from shared_context
  self.config                          → plugin's own config dict (persisted)
  self.save_config()                   → write plugin config back to disk
  self.log(msg)                        → log with plugin prefix
  self.shared_context                  → read-only snapshot of shared_context
  self.get_telemetry_snapshot()        → get current telemetry snapshot
  self.register_http_route(path, handler) → register a custom HTTP route (requires app restart)
  self.register_ui_element(element_id, html) → register a custom UI element
  self.unregister_ui_element(element_id) → unregister a custom UI element
  self.emit_custom_event(event_name, data) → emit a custom event for other plugins
  self.call_plugin_method(plugin_id, method_name, *args, **kwargs) → call another plugin's method
  self.get_plugin_list()               → get list of all loaded plugins
  self.is_plugin_loaded(plugin_id)      → check if a plugin is loaded
  self.set_runtime_config(key, value)   → set runtime config (not persisted)
  self.get_runtime_config(key, default=None) → get runtime config
  self.register_command(command_name, handler, help_text) → register a custom command
  self.unregister_command(command_name) → unregister a custom command
"""

from __future__ import annotations

import json
import os
import threading
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
        self._timers: list[threading.Timer] = []

    # ── Lifecycle hooks ───────────────────────────────────────────────────────

    def on_load(self):
        """Called once when the plugin is enabled / first loaded."""

    def on_unload(self):
        """Called before the plugin is disabled or removed."""
        for t in self._timers:
            try:
                t.cancel()
            except Exception:
                pass

    def on_app_start(self):
        """Called once after all app subsystems are initialized."""

    def on_app_shutdown(self):
        """Called during graceful application shutdown."""

    # ── Simulator hooks ───────────────────────────────────────────────────────

    def on_sim_connected(self, sim_type: str):
        """Called when a simulator connects. sim_type: 'msfs'|'xplane'|'p3d'"""

    def on_sim_disconnected(self):
        """Called when the simulator connection is lost."""

    def on_flight_phase_change(self, phase: str):
        """
        Called when the detected flight phase changes.
        phase: 'ground'|'taxi'|'climb'|'cruise'|'descent'|'approach'|'landed'
        """

    def on_altitude_crossing(self, altitude: float, direction: str):
        """
        Called when the aircraft crosses a 1000 ft altitude boundary.
        altitude: the boundary crossed (e.g. 10000.0)
        direction: 'ascending'|'descending'
        """

    def on_gear_change(self, position: str):
        """Called when landing gear position changes. position: 'up'|'down'|'transit'"""

    def on_park_brake(self, engaged: bool):
        """Called when the parking brake is engaged or released."""

    def on_engine_state(self, running: list):
        """Called when engine running state changes. running: list of 0-based engine indices."""

    def on_autopilot_change(self, engaged: bool, mode: str):
        """Called when autopilot master is toggled or mode changes."""

    def on_lights_change(self, lights: dict):
        """
        Called when any aircraft light state changes.
        lights: {'beacon': bool, 'strobe': bool, 'landing': bool, 'nav': bool, 'taxi': bool}
        """

    def on_squawk_change(self, squawk: str):
        """Called when the transponder squawk code changes."""

    def on_fuel_change(self, fuel_total: float, fuel_each: list):
        """
        Called when fuel quantity changes.
        fuel_total: total fuel in pounds/kg
        fuel_each: list of fuel quantities per tank
        """

    # ── Audio hooks ───────────────────────────────────────────────────────────

    def on_tts_speak(self, text: str):
        """Called just before TTS synthesizes text. Read-only; cannot modify."""

    def on_stt_result(self, text: str):
        """Called with the STT-recognized pilot speech after recognition."""

    def on_audio_playback_start(self, text: str):
        """Called when audio playback starts."""

    def on_audio_playback_end(self, text: str):
        """Called when audio playback ends."""

    # ── Communications hooks ──────────────────────────────────────────────────

    def on_atc_response(self, text: str, action: Optional[str]) -> Optional[str]:
        """
        Called with every ATC response before it is broadcast.
        Return a modified string to replace the text, or None to leave unchanged.
        """
        return None

    def on_pilot_input(self, text: str) -> Optional[str]:
        """
        Called with pilot speech/text before the LLM processes it.
        Return modified text or None to leave unchanged.
        """
        return None

    def on_chat_message(self, sender: str, text: str):
        """Called for every message logged in the Comms Log."""

    def on_atis_ready(self, icao: str, text: str):
        """Called when an ATIS has been generated for an airport."""

    def on_atc_action(self, action: str, params: dict):
        """
        Called when ATC issues a structured action to the pilot.
        action: e.g. 'hold_position'|'line_up_wait'|'cleared_takeoff'|'contact'|
                     'descend_to'|'climb_to'|'turn_heading'|'speed_reduce'
        params: action-specific dict, e.g. {'frequency': 119.1, 'station': 'Approach'}
        """

    def on_radio_message(self, freq: float, text: str):
        """Called when any radio message is received on a frequency."""

    def on_weather_update(self, icao: str, weather: dict):
        """Called when weather data is updated for an airport."""

    def on_traffic_update(self, icao: str, traffic: list):
        """Called when traffic data is updated for an airport."""

    def on_time_of_day_change(self, hour: int):
        """Called when simulation time changes (0-23)."""

    def on_waypoint_reached(self, waypoint: str):
        """Called when aircraft reaches a waypoint."""

    def on_route_change(self, route: str):
        """Called when flight route changes."""

    def on_sid_change(self, sid: str):
        """Called when SID changes."""

    def on_star_change(self, star: str):
        """Called when STAR changes."""

    def on_approach_change(self, approach: str):
        """Called when approach procedure changes."""

    def on_cabin_media_play(self, media_id: str):
        """Called when a cabin media entry is triggered (by UI or automation)."""

    def on_plugin_config_change(self, plugin_config: dict):
        """Called when plugin's own config changes."""

    def on_custom_event(self, event_name: str, data: any):
        """Called when a custom event is triggered by another plugin."""

    # ── Config hooks ──────────────────────────────────────────────────────────

    def on_config_change(self, config: dict):
        """Called when the user saves settings. config is the full app config dict."""

    def register_http_route(self, path: str, handler):
        """
        Register a custom HTTP route for the plugin.
        Note: Requires app restart to take effect.
        path: URL path (e.g. '/api/myplugin/data')
        handler: function that takes request and returns response
        """
        if self._manager:
            self._manager._register_http_route(self.plugin_id, path, handler)

    def register_ui_element(self, element_id: str, html: str):
        """
        Register a custom UI element to be injected into the dashboard.
        element_id: unique identifier for the element
        html: HTML content to inject
        """
        if self._socketio:
            self._socketio.emit('register_ui_element', {
                'plugin': self.plugin_id,
                'element_id': element_id,
                'html': html
            })

    def unregister_ui_element(self, element_id: str):
        """Unregister a custom UI element."""
        if self._socketio:
            self._socketio.emit('unregister_ui_element', {
                'plugin': self.plugin_id,
                'element_id': element_id
            })

    def emit_custom_event(self, event_name: str, data: any = None):
        """Emit a custom event that other plugins can listen to."""
        if self._event_bus:
            self._event_bus.emit(f'plugin_custom_{self.plugin_id}_{event_name}', data)

    def call_plugin_method(self, plugin_id: str, method_name: str, *args, **kwargs):
        """Call a method on another plugin."""
        if self._manager:
            target_plugin = self._manager.get_instance(plugin_id)
            if target_plugin and hasattr(target_plugin, method_name):
                return getattr(target_plugin, method_name)(*args, **kwargs)
        return None

    def get_plugin_list(self) -> list:
        """Get list of all loaded plugins."""
        if self._manager:
            return self._manager.list_plugins()
        return []

    def is_plugin_loaded(self, plugin_id: str) -> bool:
        """Check if a plugin is loaded."""
        if self._manager:
            instance = self._manager.get_instance(plugin_id)
            return instance is not None
        return False

    def set_runtime_config(self, key: str, value: any):
        """Set a runtime config value (not persisted)."""
        if not hasattr(self, '_runtime_config'):
            self._runtime_config = {}
        self._runtime_config[key] = value

    def get_runtime_config(self, key: str, default=None):
        """Get a runtime config value."""
        if hasattr(self, '_runtime_config'):
            return self._runtime_config.get(key, default)
        return default

    def register_command(self, command_name: str, handler, help_text: str = ''):
        """
        Register a custom command that can be called via API or UI.
        command_name: name of the command
        handler: function to execute when command is called
        help_text: description of the command
        """
        if self._manager:
            self._manager._register_command(self.plugin_id, command_name, handler, help_text)

    def unregister_command(self, command_name: str):
        """Unregister a custom command."""
        if self._manager:
            self._manager._unregister_command(self.plugin_id, command_name)

    # ── Dashboard appearance API ──────────────────────────────────────────────

    def inject_css(self, css: str, style_id: str = None):
        """
        Inject custom CSS into the dashboard.
        css: CSS text to inject (scoped by plugin, applied as <style> tag)
        style_id: optional unique ID to allow updating later (defaults to plugin_id)
        Plugins are encouraged to scope their CSS with .of-plugin-<plugin_id> {}
        to avoid conflicts.
        """
        if self._socketio:
            self._socketio.emit('plugin_inject_css', {
                'plugin': self.plugin_id,
                'style_id': style_id or self.plugin_id,
                'css': css,
            })

    def remove_css(self, style_id: str = None):
        """Remove previously injected CSS."""
        if self._socketio:
            self._socketio.emit('plugin_remove_css', {
                'plugin': self.plugin_id,
                'style_id': style_id or self.plugin_id,
            })

    def inject_panel(self, panel_id: str, html: str, position: str = 'sidebar',
                     title: str = '', icon: str = '🔌'):
        """
        Inject a custom panel/widget into the dashboard.
        panel_id: unique identifier (plugin-scoped)
        html:     HTML content for the panel body
        position: 'sidebar' | 'map-overlay' | 'chat-below' | 'modal'
        title:    panel title (shown in header)
        icon:     emoji or text icon for the panel header
        """
        if self._socketio:
            self._socketio.emit('plugin_inject_panel', {
                'plugin': self.plugin_id,
                'panel_id': f"{self.plugin_id}_{panel_id}",
                'html': html,
                'position': position,
                'title': title,
                'icon': icon,
            })

    def remove_panel(self, panel_id: str):
        """Remove a previously injected panel."""
        if self._socketio:
            self._socketio.emit('plugin_remove_panel', {
                'plugin': self.plugin_id,
                'panel_id': f"{self.plugin_id}_{panel_id}",
            })

    def set_map_layer(self, layer_id: str, geojson: dict, style: dict = None):
        """
        Add or update a GeoJSON layer on the map.
        layer_id: unique identifier
        geojson:  GeoJSON FeatureCollection or Feature
        style:    Leaflet path options (color, weight, opacity, etc.)
        """
        if self._socketio:
            self._socketio.emit('plugin_set_map_layer', {
                'plugin': self.plugin_id,
                'layer_id': f"{self.plugin_id}_{layer_id}",
                'geojson': geojson,
                'style': style or {},
            })

    def remove_map_layer(self, layer_id: str):
        """Remove a map layer."""
        if self._socketio:
            self._socketio.emit('plugin_remove_map_layer', {
                'plugin': self.plugin_id,
                'layer_id': f"{self.plugin_id}_{layer_id}",
            })

    def set_map_marker(self, marker_id: str, lat: float, lon: float,
                       label: str = '', icon_html: str = None, popup_html: str = None):
        """Add or update a custom map marker."""
        if self._socketio:
            self._socketio.emit('plugin_set_map_marker', {
                'plugin': self.plugin_id,
                'marker_id': f"{self.plugin_id}_{marker_id}",
                'lat': lat, 'lon': lon,
                'label': label,
                'icon_html': icon_html,
                'popup_html': popup_html,
            })

    def remove_map_marker(self, marker_id: str):
        """Remove a custom map marker."""
        if self._socketio:
            self._socketio.emit('plugin_remove_map_marker', {
                'plugin': self.plugin_id,
                'marker_id': f"{self.plugin_id}_{marker_id}",
            })

    def set_status_bar_item(self, item_id: str, text: str, color: str = '#fff',
                             bg: str = 'rgba(0,0,0,0.5)', tooltip: str = ''):
        """
        Add or update an item in the dashboard status bar.
        item_id: unique identifier
        text:    display text (emoji + text OK)
        color:   text color (CSS value)
        bg:      background color (CSS value)
        tooltip: hover tooltip text
        """
        if self._socketio:
            self._socketio.emit('plugin_set_status_bar', {
                'plugin': self.plugin_id,
                'item_id': f"{self.plugin_id}_{item_id}",
                'text': text, 'color': color, 'bg': bg, 'tooltip': tooltip,
            })

    def clear_status_bar_item(self, item_id: str):
        """Remove a status bar item."""
        if self._socketio:
            self._socketio.emit('plugin_clear_status_bar', {
                'plugin': self.plugin_id,
                'item_id': f"{self.plugin_id}_{item_id}",
            })

    def on_dashboard_action(self, action_id: str, payload: dict):
        """
        Called when a user clicks a button/element injected by this plugin.
        action_id: the data-of-action value set in injected HTML
        payload:   any data-of-* attributes collected from the element
        """

    # ── Telemetry / frequency ─────────────────────────────────────────────────

    def on_frequency_change(self, frequency: float):
        """Called when the pilot tunes a new COM1 frequency."""

    def on_telemetry(self, data: dict):
        """Called with the latest telemetry snapshot (~10 Hz)."""

    # ── Plugin API ────────────────────────────────────────────────────────────

    def emit(self, event: str, data: Any = None):
        """Emit a socket.io event to all connected dashboards."""
        if self._socketio:
            self._socketio.emit(event, data)

    def subscribe(self, event: str, callback):
        """Subscribe to an internal EventBus event."""
        if self._event_bus:
            self._event_bus.on(event, callback)

    def speak(self, text: str, priority: int = 1):
        """
        Trigger TTS synthesis. priority: 1=critical/ATC, 2=alert, 3=background.
        Runs asynchronously; returns immediately.
        """
        if self._event_bus:
            self._event_bus.emit('tts_request', text)

    def notify(self, title: str, body: str = '', level: str = 'info'):
        """
        Send a toast notification to all connected dashboards.
        level: 'info'|'success'|'warning'|'danger'
        """
        if self._socketio:
            self._socketio.emit('plugin_notify', {
                'plugin': self.plugin_name,
                'title': title,
                'body': body,
                'level': level,
            })

    def schedule(self, interval_sec: float, fn, repeat: bool = True):
        """
        Call fn() after interval_sec seconds. If repeat=True, keeps repeating.
        Returns a cancel handle (call handle.cancel() to stop).
        """
        def _run():
            try:
                fn()
            except Exception as e:
                self.log(f"schedule error: {e}")
            if repeat:
                t = _make_timer()
                self._timers.append(t)
                t.start()

        def _make_timer():
            return threading.Timer(interval_sec, _run)

        t = _make_timer()
        self._timers.append(t)
        t.start()
        return t

    def inject_atc_message(self, text: str):
        """Push a synthetic ATC message into the comms log and TTS queue."""
        if self._event_bus:
            self._event_bus.emit('atc_inject', text)

    def get_config_value(self, key_path: str, default=None):
        """
        Read a value from the app config using dot notation.
        e.g. self.get_config_value('audio.tts_voice', 'en-US-GuyNeural')
        """
        if not self._manager:
            return default
        cfg = self._manager.config
        for key in key_path.split('.'):
            if not isinstance(cfg, dict):
                return default
            cfg = cfg.get(key, default)
        return cfg

    def set_context_value(self, section: str, key: str, value: Any):
        """
        Write a value into shared_context[section][key] under the context lock.
        section: e.g. 'aircraft', 'atc_state', 'environment'
        """
        if self._ctx_lock and self._shared_ctx:
            with self._ctx_lock:
                if section not in self._shared_ctx:
                    self._shared_ctx[section] = {}
                self._shared_ctx[section][key] = value

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

    def get_telemetry_snapshot(self) -> dict:
        """Get current telemetry snapshot."""
        return self.shared_context

    def get_context_value(self, section: str, key: str, default=None):
        """Read a value from shared_context."""
        snapshot = self.shared_context
        return snapshot.get(section, {}).get(key, default)

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
