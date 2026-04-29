"""
plugin_manager.py 鈥?Community plugin discovery, loading, and lifecycle management.

Plugin folder layout expected:
    plugins/
      community/
        <plugin_id>/
          manifest.json
          plugin.py          鈫?entry-point; must contain class Plugin(OpenFrequencyPlugin)
          config.json        鈫?auto-created by plugin when it saves config
          ...

Usage (from app.py):
    plugin_manager = PluginManager(config, socketio, event_bus, context_lock, shared_context)
    plugin_manager.discover()
    plugin_manager.fire_app_start()   # call after all subsystems are up
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
        self._last_install_warning = None  # Store last installation warning

        # { plugin_id: { 'manifest': dict, 'instance': OpenFrequencyPlugin|None,
        #                 'enabled': bool, 'error': str|None, 'path': str } }
        self._plugins: dict[str, dict] = {}

        # Telemetry state tracking for change-detection hooks
        self._prev_gear: Optional[str] = None
        self._prev_brake: Optional[bool] = None
        self._prev_engines: Optional[list] = None
        self._prev_ap: Optional[tuple] = None   # (engaged, mode)
        self._prev_lights: Optional[dict] = None
        self._prev_squawk: Optional[str] = None
        self._prev_phase: Optional[str] = None
        self._prev_alt_band: Optional[int] = None  # floor(alt/1000)

        self._plugins_root = self._find_plugins_root()
        print(f"PluginManager: community folder 鈫?{self._plugins_root}")

        # Custom HTTP routes and commands registry
        self._http_routes = {}  # {plugin_id: {path: handler}}
        self._commands = {}     # {plugin_id: {command_name: (handler, help_text)}}

        # Subscribe to event_bus for hooks that map 1:1 to events
        self._subscribe_events()

    # 鈹€鈹€ Event subscriptions 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def _subscribe_events(self):
        self.event_bus.on('sim_connected',   self._on_sim_connected)
        self.event_bus.on('sim_disconnected',self._on_sim_disconnected)
        self.event_bus.on('config_updated',  self._on_config_change)
        self.event_bus.on('stt_result',      self._on_stt_result)
        self.event_bus.on('atis_ready',      self._on_atis_ready)
        self.event_bus.on('atc_action',      self._on_atc_action)
        self.event_bus.on('atc_inject',      self._on_atc_inject)
        self.event_bus.on('app_shutdown',    self._on_app_shutdown)

    def _on_sim_connected(self, sim_type: str = 'unknown'):
        self.hook_sim_connected(sim_type)

    def _on_sim_disconnected(self):
        self.hook_sim_disconnected()

    def _on_config_change(self, new_config: dict):
        self.hook_config_change(new_config)

    def _on_stt_result(self, text: str):
        self.hook_stt_result(text)

    def _on_atis_ready(self, icao: str, text: str = ''):
        self.hook_atis_ready(icao, text)

    def _on_atc_action(self, action: str, params: dict = None):
        self.hook_atc_action(action, params or {})

    def _on_atc_inject(self, text: str):
        """Route plugin-injected ATC messages into the comms log."""
        try:
            self.event_bus.emit('tts_request', text)
            self.socketio.emit('atc_message', {'text': text, 'source': 'plugin'})
        except Exception as e:
            print(f"PluginManager: atc_inject error: {e}")

    def _on_app_shutdown(self):
        self.hook_app_shutdown()

    # 鈹€鈹€ Discovery 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def _find_plugins_root(self) -> str:
        """Locate plugins/community relative to the application root."""
        candidates = [
            # PyInstaller packaged: next to the exe
            os.path.join(os.path.dirname(sys.executable), 'plugins', _COMMUNITY_DIR_NAME),
            # Development: next to this file (core/ 鈫?project root 鈫?plugins/)
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

    # 鈹€鈹€ Load / Unload 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

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

            # 鈹€鈹€ Register plugin cabin media 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
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

    # 鈹€鈹€ Enable / Disable / Uninstall 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

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

                # Compliance check - validate manifest and check for suspicious patterns
                compliance_result = self._check_plugin_compliance(manifest, names)
                if not compliance_result[0]:
                    return False, f"Plugin non-compliant: {compliance_result[1]}"

                # Check for warning level
                warning_level = compliance_result[2]
                if warning_level == 'warn':
                    # Store warning for later display
                    self._last_install_warning = compliance_result[1]

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

    def _check_plugin_compliance(self, manifest: dict, zip_names: list) -> tuple[bool, str, str]:
        """
        Check if a plugin is compliant with OpenFrequency requirements.
        Returns (is_compliant, message, warning_level).
        warning_level: 'none', 'warn', 'error'
        """
        # Required manifest fields
        required_fields = ['id', 'name', 'version', 'author']
        for field in required_fields:
            if not manifest.get(field):
                return False, f"Missing required manifest field: {field}", 'error'

        plugin_id = manifest.get('id', '')

        # Check minimum app version if specified
        min_version = manifest.get('min_app_version')
        if min_version:
            from packaging import version as pkg_version
            try:
                current_version = self._get_current_app_version()
                if pkg_version.parse(current_version) < pkg_version.parse(min_version):
                    # Check if required features are available
                    required_features = self._check_required_features(manifest)
                    if required_features['all_available']:
                        # Features available, warn but allow
                        return True, f"Plugin requires OpenFrequency v{min_version} or higher (current: v{current_version}), but all required features are available. Installation will proceed with warning.", 'warn'
                    else:
                        # Critical features missing, block
                        missing = ', '.join(required_features['missing'])
                        return False, f"Plugin requires OpenFrequency v{min_version} or higher (current: v{current_version}). Missing required features: {missing}. Cannot install.", 'error'
            except Exception as e:
                return False, f"Invalid version format in min_app_version: {e}", 'error'

        # Check for suspicious file patterns
        suspicious_patterns = [
            '../',  # Path traversal
            '..\\',  # Windows path traversal
            '.exe',  # Executable files
            '.dll',  # Dynamic libraries
            '.bat',  # Batch files
            '.cmd',  # Command files
            '.sh',   # Shell scripts
            '.scr',  # Screensaver
            '.vbs',  # VBScript
            '.js',   # JavaScript (could be malicious)
        ]

        for name in zip_names:
            name_lower = name.lower()
            for pattern in suspicious_patterns:
                if pattern in name_lower and not name_lower.endswith('.json'):
                    return False, f"Suspicious file detected: {name}", 'error'

        # Check for allowed entry point file types
        entry_point = manifest.get('entry_point', '')
        if entry_point:
            allowed_extensions = ['.py']
            if not any(entry_point.endswith(ext) for ext in allowed_extensions):
                return False, f"Entry point must be a Python file (.py), got: {entry_point}", 'error'

        # Check cabin scripts - follow data/cabin/scripts.json format
        cabin_scripts = manifest.get('cabin_scripts', {})
        for airline_code, scripts in cabin_scripts.items():
            if not isinstance(scripts, dict):
                return False, f"Cabin scripts for '{airline_code}' must be a dictionary", 'error'
            # Validate script keys match expected format
            expected_keys = {'voice', 'welcome', 'door_close', 'safety_demo', 'takeoff_prep',
                            'climb_service', 'descent', 'landing_prep', 'arrival_prep',
                            'turbulence', 'deboarding'}
            for key in scripts.keys():
                if key not in expected_keys:
                    return False, f"Unknown cabin script key '{key}' in '{airline_code}'", 'error'
            # Validate each script value
            for phase, value in scripts.items():
                if phase == 'voice':
                    if not isinstance(value, str):
                        return False, f"'voice' must be a string in '{airline_code}'", 'error'
                else:
                    # Other phases can be string or object with text/video
                    if isinstance(value, dict):
                        if 'text' not in value:
                            return False, f"Cabin script '{phase}' in '{airline_code}' must have 'text' field", 'error'
                    elif not isinstance(value, str):
                        return False, f"Cabin script '{phase}' in '{airline_code}' must be string or dict", 'error'

        # Check for safe hooks (whitelist)
        allowed_hooks = {
            'on_load', 'on_unload', 'on_sim_connect', 'on_sim_disconnect',
            'on_tts_request', 'on_audio_play', 'on_radio_transmit',
            'on_radio_receive', 'on_config_change', 'on_telemetry_update',
            'on_flight_plan_update', 'on_cabin_event', 'on_emergency'
        }
        manifest_hooks = manifest.get('hooks', [])
        for hook in manifest_hooks:
            if hook not in allowed_hooks:
                return False, f"Unauthorized hook: {hook}", 'error'

        # Check API permissions
        api_permissions = manifest.get('api_permissions', [])
        dangerous_permissions = ['file_write_system', 'network_unrestricted', 'code_execute']
        for perm in api_permissions:
            if perm in dangerous_permissions:
                return False, f"Dangerous API permission requested: {perm}", 'error'

        return True, "Compliant", 'none'

    def _check_required_features(self, manifest: dict) -> dict:
        """
        Check if required features/API endpoints are available in current version.
        Returns {'all_available': bool, 'missing': list}
        """
        current_version = self._get_current_app_version()
        hooks = manifest.get('hooks', [])
        cabin_scripts = manifest.get('cabin_scripts', {})

        # Map features to minimum versions
        feature_versions = {
            'on_cabin_media_play': '3.9.0',
            'cabin_scripts': '3.9.0',
            'min_app_version': '3.9.0',
        }

        missing = []

        # Check hooks that require specific versions
        for hook in hooks:
            min_ver = feature_versions.get(hook)
            if min_ver:
                from packaging import version as pkg_version
                if pkg_version.parse(current_version) < pkg_version.parse(min_ver):
                    missing.append(f"{hook} (requires v{min_ver}+)")

        # Check cabin_scripts features
        if cabin_scripts:
            min_ver = feature_versions.get('cabin_scripts')
            from packaging import version as pkg_version
            if pkg_version.parse(current_version) < pkg_version.parse(min_ver):
                missing.append(f"cabin_scripts (requires v{min_ver}+)")

        return {
            'all_available': len(missing) == 0,
            'missing': missing
        }

    def _get_current_app_version(self) -> str:
        """Get the current OpenFrequency version."""
        try:
            version_file = Path(__file__).parent.parent / 'version.txt'
            if version_file.exists():
                return version_file.read_text().strip()
        except Exception:
            pass
        return "0.0.0"

    def _persist_disabled_list(self):
        """Write disabled plugin IDs back to config (in-memory only; caller must save)."""
        with self._lock:
            disabled = [pid for pid, r in self._plugins.items() if not r['enabled']]
        if 'plugins' not in self.config:
            self.config['plugins'] = {}
        self.config['plugins']['disabled'] = disabled

    # 鈹€鈹€ Query 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

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

    # 鈹€鈹€ App lifecycle 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def fire_app_start(self):
        """Call after all subsystems are initialized."""
        for inst in self._instances():
            try:
                inst.on_app_start()
            except Exception as e:
                print(f"PluginManager: on_app_start error in '{inst.plugin_id}': {e}")

    # 鈹€鈹€ Hook routing 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

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
        """Call on_telemetry for all plugins, plus detect change-based hooks."""
        instances = self._instances()
        for inst in instances:
            try:
                inst.on_telemetry(data)
            except Exception:
                pass

        # 鈹€鈹€ Change detection for derived hooks 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
        self._detect_gear_change(data, instances)
        self._detect_park_brake(data, instances)
        self._detect_engine_state(data, instances)
        self._detect_autopilot(data, instances)
        self._detect_lights(data, instances)
        self._detect_squawk(data, instances)
        self._detect_flight_phase(data, instances)
        self._detect_altitude_crossing(data, instances)

    def _detect_gear_change(self, data: dict, instances):
        gear_pct = data.get('gear_position', None)
        if gear_pct is None:
            return
        if gear_pct > 0.9:
            pos = 'down'
        elif gear_pct < 0.1:
            pos = 'up'
        else:
            pos = 'transit'
        if pos != self._prev_gear:
            self._prev_gear = pos
            for inst in instances:
                try:
                    inst.on_gear_change(pos)
                except Exception:
                    pass

    def _detect_park_brake(self, data: dict, instances):
        engaged = bool(data.get('park_brake', False))
        if engaged != self._prev_brake:
            self._prev_brake = engaged
            for inst in instances:
                try:
                    inst.on_park_brake(engaged)
                except Exception:
                    pass

    def _detect_engine_state(self, data: dict, instances):
        raw = data.get('engines_running', None)
        if raw is None:
            return
        running = [i for i, v in enumerate(raw) if v]
        if running != self._prev_engines:
            self._prev_engines = running
            for inst in instances:
                try:
                    inst.on_engine_state(running)
                except Exception:
                    pass

    def _detect_autopilot(self, data: dict, instances):
        engaged = bool(data.get('autopilot', False))
        mode    = str(data.get('autopilot_mode', ''))
        state   = (engaged, mode)
        if state != self._prev_ap:
            self._prev_ap = state
            for inst in instances:
                try:
                    inst.on_autopilot_change(engaged, mode)
                except Exception:
                    pass

    def _detect_lights(self, data: dict, instances):
        lights = {
            'beacon':  bool(data.get('light_beacon',  False)),
            'strobe':  bool(data.get('light_strobe',  False)),
            'landing': bool(data.get('light_landing', False)),
            'nav':     bool(data.get('light_nav',     False)),
            'taxi':    bool(data.get('light_taxi',    False)),
        }
        if lights != self._prev_lights:
            self._prev_lights = lights
            for inst in instances:
                try:
                    inst.on_lights_change(dict(lights))
                except Exception:
                    pass

    def _detect_squawk(self, data: dict, instances):
        squawk = str(data.get('transponder', '') or '')
        if squawk and squawk != self._prev_squawk:
            self._prev_squawk = squawk
            for inst in instances:
                try:
                    inst.on_squawk_change(squawk)
                except Exception:
                    pass

    def _detect_flight_phase(self, data: dict, instances):
        phase = data.get('flight_phase', None)
        if phase and phase != self._prev_phase:
            self._prev_phase = phase
            for inst in instances:
                try:
                    inst.on_flight_phase_change(phase)
                except Exception:
                    pass

    def _detect_altitude_crossing(self, data: dict, instances):
        alt = data.get('altitude', None)
        if alt is None:
            return
        band = int(alt // 1000)
        if self._prev_alt_band is not None and band != self._prev_alt_band:
            boundary = band * 1000 if band > self._prev_alt_band else self._prev_alt_band * 1000
            direction = 'ascending' if band > self._prev_alt_band else 'descending'
            for inst in instances:
                try:
                    inst.on_altitude_crossing(float(boundary), direction)
                except Exception:
                    pass
        self._prev_alt_band = band

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

    def hook_tts_speak(self, text: str):
        for inst in self._instances():
            try:
                inst.on_tts_speak(text)
            except Exception:
                pass

    def hook_sim_connected(self, sim_type: str):
        for inst in self._instances():
            try:
                inst.on_sim_connected(sim_type)
            except Exception:
                pass

    def hook_sim_disconnected(self):
        for inst in self._instances():
            try:
                inst.on_sim_disconnected()
            except Exception:
                pass

    def hook_stt_result(self, text: str):
        for inst in self._instances():
            try:
                inst.on_stt_result(text)
            except Exception:
                pass

    def hook_atis_ready(self, icao: str, text: str):
        for inst in self._instances():
            try:
                inst.on_atis_ready(icao, text)
            except Exception:
                pass

    def hook_atc_action(self, action: str, params: dict):
        for inst in self._instances():
            try:
                inst.on_atc_action(action, params)
            except Exception:
                pass

    def hook_config_change(self, config: dict):
        for inst in self._instances():
            try:
                inst.on_config_change(config)
            except Exception:
                pass

    def hook_app_shutdown(self):
        for inst in self._instances():
            try:
                inst.on_app_shutdown()
            except Exception:
                pass

    # 鈹€鈹€ Custom HTTP Routes 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def _register_http_route(self, plugin_id: str, path: str, handler):
        """Register a custom HTTP route for a plugin."""
        with self._lock:
            if plugin_id not in self._http_routes:
                self._http_routes[plugin_id] = {}
            self._http_routes[plugin_id][path] = handler
        print(f"PluginManager: Registered HTTP route '{path}' for plugin '{plugin_id}'")

    def get_http_routes(self) -> dict:
        """Get all registered HTTP routes."""
        with self._lock:
            return dict(self._http_routes)

    # 鈹€鈹€ Custom Commands 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def _register_command(self, plugin_id: str, command_name: str, handler, help_text: str):
        """Register a custom command for a plugin."""
        with self._lock:
            if plugin_id not in self._commands:
                self._commands[plugin_id] = {}
            self._commands[plugin_id][command_name] = (handler, help_text)
        print(f"PluginManager: Registered command '{command_name}' for plugin '{plugin_id}'")

    def _unregister_command(self, plugin_id: str, command_name: str):
        """Unregister a custom command."""
        with self._lock:
            if plugin_id in self._commands and command_name in self._commands[plugin_id]:
                del self._commands[plugin_id][command_name]
        print(f"PluginManager: Unregistered command '{command_name}' for plugin '{plugin_id}'")

    def get_commands(self) -> dict:
        """Get all registered commands."""
        with self._lock:
            return dict(self._commands)

    def execute_command(self, plugin_id: str, command_name: str, *args, **kwargs):
        """Execute a registered command."""
        with self._lock:
            if plugin_id in self._commands and command_name in self._commands[plugin_id]:
                handler, _ = self._commands[plugin_id][command_name]
                try:
                    return handler(*args, **kwargs)
                except Exception as e:
                    print(f"PluginManager: Command execution error: {e}")
                    return None
        return None


# 鈹€鈹€ Singleton (created in app.py) 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
plugin_manager: Optional[PluginManager] = None
