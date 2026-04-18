"""
plugin_manager.py — Community plugin discovery, loading, and lifecycle management.

Plugin folder layout expected:
    plugins/
      community/
        <plugin_id>/
          manifest.json
          plugin.py          ← entry-point; must contain class Plugin(OpenFrequencyPlugin)
          config.json        ← auto-created by plugin when it saves config
          ...

Usage (from app.py):
    plugin_manager = PluginManager(config, socketio, event_bus, context_lock, shared_context)
    plugin_manager.discover()
    # Hooks are called by logic_manager / tts_engine via event_bus events.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from pathlib import Path
from typing import Optional

from .plugin_api import OpenFrequencyPlugin


_COMMUNITY_DIR_NAME = 'community'


class PluginManager:
    """Discovers, loads, and routes events to community plugins."""

    def __init__(self, config: dict, socketio, event_bus, context_lock, shared_context):
        self.config        = config
        self.socketio      = socketio
        self.event_bus     = event_bus
        self.context_lock  = context_lock
        self.shared_context= shared_context
        self._lock         = threading.Lock()

        # { plugin_id: { 'manifest': dict, 'instance': OpenFrequencyPlugin|None,
        #                 'enabled': bool, 'error': str|None, 'path': str } }
        self._plugins: dict[str, dict] = {}

        self._plugins_root = self._find_plugins_root()
        print(f"PluginManager: community folder → {self._plugins_root}")

    # ── Discovery ─────────────────────────────────────────────────────────────

    def _find_plugins_root(self) -> str:
        """Locate plugins/community relative to the application root."""
        candidates = [
            # PyInstaller packaged: next to the exe
            os.path.join(os.path.dirname(sys.executable), 'plugins', _COMMUNITY_DIR_NAME),
            # Development: next to this file (core/ → project root → plugins/)
            os.path.join(os.path.dirname(__file__), '..', 'plugins', _COMMUNITY_DIR_NAME),
        ]
        for c in candidates:
            c = os.path.normpath(c)
            if os.path.isdir(c):
                return c
        # Fall back to dev path (will be created on first install)
        return os.path.normpath(candidates[1])

    def discover(self):
        """Scan the community folder and register all manifests."""
        if not os.path.isdir(self._plugins_root):
            os.makedirs(self._plugins_root, exist_ok=True)
            return

        disabled_ids: set[str] = set(
            self.config.get('plugins', {}).get('disabled', [])
        )

        for entry in sorted(os.scandir(self._plugins_root), key=lambda e: e.name):
            if not entry.is_dir():
                continue
            manifest_path = os.path.join(entry.path, 'manifest.json')
            if not os.path.exists(manifest_path):
                continue
            try:
                with open(manifest_path, encoding='utf-8') as f:
                    manifest = json.load(f)
            except Exception as e:
                print(f"PluginManager: Cannot read manifest at {manifest_path}: {e}")
                continue

            plugin_id = manifest.get('id', entry.name)
            enabled   = plugin_id not in disabled_ids

            with self._lock:
                self._plugins[plugin_id] = {
                    'manifest': manifest,
                    'instance': None,
                    'enabled':  enabled,
                    'error':    None,
                    'path':     entry.path,
                }

            if enabled:
                self._load(plugin_id)

        print(f"PluginManager: {len(self._plugins)} plugin(s) discovered.")

    # ── Load / Unload ─────────────────────────────────────────────────────────

    def _load(self, plugin_id: str):
        with self._lock:
            record = self._plugins.get(plugin_id)
        if not record:
            return

        manifest   = record['manifest']
        plugin_dir = record['path']
        entry_file = manifest.get('entry', 'plugin.py')
        entry_path = os.path.join(plugin_dir, entry_file)

        if not os.path.exists(entry_path):
            self._set_error(plugin_id, f"Entry file not found: {entry_file}")
            return

        try:
            spec   = importlib.util.spec_from_file_location(
                f"of_plugin_{plugin_id}", entry_path
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            PluginClass = getattr(module, 'Plugin', None)
            if PluginClass is None:
                self._set_error(plugin_id, "No class named 'Plugin' found in entry file.")
                return
            if not issubclass(PluginClass, OpenFrequencyPlugin):
                self._set_error(plugin_id, "'Plugin' must subclass OpenFrequencyPlugin.")
                return

            # Inject framework references
            PluginClass._manager    = self
            PluginClass._socketio   = self.socketio
            PluginClass._event_bus  = self.event_bus
            PluginClass._ctx_lock   = self.context_lock
            PluginClass._shared_ctx = self.shared_context
            PluginClass._plugin_dir = plugin_dir

            instance = PluginClass()
            instance.plugin_id   = manifest.get('id', plugin_id)
            instance.plugin_name = manifest.get('name', plugin_id)
            instance.version     = manifest.get('version', '0.0.0')
            instance.author      = manifest.get('author', '')
            instance.description = manifest.get('description', '')
            instance._plugin_dir = plugin_dir
            instance._load_config()

            instance.on_load()

            with self._lock:
                self._plugins[plugin_id]['instance'] = instance
                self._plugins[plugin_id]['error']    = None

            # ── Register plugin cabin media ─────────────────────────────────
            cabin_entries = manifest.get('cabin_media', [])
            if cabin_entries:
                try:
                    from .cabin_media_manager import cabin_media_manager
                    cabin_media_manager.register_plugin_media(plugin_dir, cabin_entries)
                    print(f"PluginManager: Registered {len(cabin_entries)} cabin media from '{plugin_id}'")
                except Exception as cm_err:
                    print(f"PluginManager: Cabin media registration failed for '{plugin_id}': {cm_err}")

            print(f"PluginManager: Loaded '{instance.plugin_name}' v{instance.version}")
        except Exception as e:
            self._set_error(plugin_id, str(e))
            print(f"PluginManager: Failed to load '{plugin_id}': {e}")

    def _unload(self, plugin_id: str):
        with self._lock:
            record = self._plugins.get(plugin_id)
        if not record or not record['instance']:
            return
        try:
            record['instance'].on_unload()
        except Exception:
            pass
        with self._lock:
            self._plugins[plugin_id]['instance'] = None

    def _set_error(self, plugin_id: str, msg: str):
        with self._lock:
            if plugin_id in self._plugins:
                self._plugins[plugin_id]['error']   = msg
                self._plugins[plugin_id]['enabled'] = False

    # ── Enable / Disable / Uninstall ──────────────────────────────────────────

    def enable(self, plugin_id: str) -> bool:
        with self._lock:
            if plugin_id not in self._plugins:
                return False
            self._plugins[plugin_id]['enabled'] = True
        self._load(plugin_id)
        self._persist_disabled_list()
        return True

    def disable(self, plugin_id: str) -> bool:
        self._unload(plugin_id)
        with self._lock:
            if plugin_id not in self._plugins:
                return False
            self._plugins[plugin_id]['enabled'] = False
        self._persist_disabled_list()
        return True

    def uninstall(self, plugin_id: str) -> bool:
        """Remove the plugin folder from disk."""
        self._unload(plugin_id)
        with self._lock:
            record = self._plugins.pop(plugin_id, None)
        if not record:
            return False
        import shutil
        try:
            shutil.rmtree(record['path'], ignore_errors=True)
            print(f"PluginManager: Uninstalled '{plugin_id}'")
            return True
        except Exception as e:
            print(f"PluginManager: Uninstall error for '{plugin_id}': {e}")
            return False

    def install_from_zip(self, zip_path: str) -> tuple[bool, str]:
        """
        Extract a plugin ZIP into the community folder.
        The ZIP must contain a manifest.json at root or inside one sub-folder.
        Returns (success, message).
        """
        import zipfile, shutil, tempfile

        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                names = zf.namelist()
                # Detect manifest location
                manifest_entries = [n for n in names if n.endswith('manifest.json')
                                    and n.count('/') <= 1]
                if not manifest_entries:
                    return False, "No manifest.json found in ZIP."

                manifest_entry = min(manifest_entries, key=lambda n: n.count('/'))
                prefix = os.path.dirname(manifest_entry)

                # Read manifest to get plugin id
                with zf.open(manifest_entry) as mf:
                    manifest = json.load(mf)
                plugin_id = manifest.get('id')
                if not plugin_id:
                    return False, "manifest.json missing 'id' field."

                dest = os.path.join(self._plugins_root, plugin_id)
                os.makedirs(dest, exist_ok=True)

                # Extract files
                with tempfile.TemporaryDirectory() as tmp:
                    zf.extractall(tmp)
                    src = os.path.join(tmp, prefix) if prefix else tmp
                    if os.path.isdir(dest):
                        shutil.rmtree(dest)
                    shutil.copytree(src, dest)

            # Re-discover this plugin
            self.discover()
            return True, f"Plugin '{plugin_id}' installed successfully."
        except Exception as e:
            return False, str(e)

    def _persist_disabled_list(self):
        """Write disabled plugin IDs back to config (in-memory only; caller must save)."""
        with self._lock:
            disabled = [pid for pid, r in self._plugins.items() if not r['enabled']]
        if 'plugins' not in self.config:
            self.config['plugins'] = {}
        self.config['plugins']['disabled'] = disabled

    # ── Query ─────────────────────────────────────────────────────────────────

    def list_plugins(self) -> list[dict]:
        """Return serialisable list of all known plugins."""
        with self._lock:
            result = []
            for pid, r in self._plugins.items():
                m = r['manifest']
                result.append({
                    'id':          pid,
                    'name':        m.get('name', pid),
                    'version':     m.get('version', '?'),
                    'author':      m.get('author', ''),
                    'description': m.get('description', ''),
                    'enabled':     r['enabled'],
                    'loaded':      r['instance'] is not None,
                    'error':       r['error'],
                    'path':        r['path'],
                    'hooks':       m.get('hooks', []),
                })
            return result

    def get_instance(self, plugin_id: str) -> Optional[OpenFrequencyPlugin]:
        with self._lock:
            return self._plugins.get(plugin_id, {}).get('instance')

    # ── Hook routing ──────────────────────────────────────────────────────────

    def _instances(self):
        with self._lock:
            return [r['instance'] for r in self._plugins.values()
                    if r['enabled'] and r['instance'] is not None]

    def hook_atc_response(self, text: str, action) -> str:
        """Run on_atc_response hooks; each plugin may modify the text."""
        for inst in self._instances():
            try:
                result = inst.on_atc_response(text, action)
                if result is not None:
                    text = result
            except Exception as e:
                print(f"PluginManager: hook_atc_response error in '{inst.plugin_id}': {e}")
        return text

    def hook_telemetry(self, data: dict):
        for inst in self._instances():
            try:
                inst.on_telemetry(data)
            except Exception:
                pass

    def hook_frequency_change(self, freq: float):
        for inst in self._instances():
            try:
                inst.on_frequency_change(freq)
            except Exception:
                pass

    def hook_pilot_input(self, text: str) -> str:
        for inst in self._instances():
            try:
                result = inst.on_pilot_input(text)
                if result is not None:
                    text = result
            except Exception:
                pass
        return text

    def hook_chat_message(self, sender: str, text: str):
        for inst in self._instances():
            try:
                inst.on_chat_message(sender, text)
            except Exception:
                pass

    def hook_cabin_media_play(self, media_id: str):
        for inst in self._instances():
            try:
                inst.on_cabin_media_play(media_id)
            except Exception:
                pass


# ── Singleton (created in app.py) ─────────────────────────────────────────────
plugin_manager: Optional[PluginManager] = None
