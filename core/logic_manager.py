import threading
import time
import random
import os
from .context import shared_context, context_lock, event_bus
from .immersion.workload_sim import WorkloadSimulator
from .taxi_router import TaxiRouter
from .instruction_extractor import InstructionExtractor
from .quick_reply import QuickReplyEngine

class LogicManager:
    """
    The central coordinator. Does not own other modules.
    It subscribes to events on the EventBus and emits data to the UI via SocketIO.
    """
    def __init__(self, config, socketio, airport_frequency_service=None, ground_service=None):
        self.config = config
        self.socketio = socketio
        self.airport_frequency_service = airport_frequency_service
        self.ground_service = ground_service
        self.taxi_router = TaxiRouter(ground_service) if ground_service else None
        self.workload_sim = WorkloadSimulator(config)
        self.scheduler = None
        self.last_freq = 0.0
        self.message_history = [] # Buffer for chat log
        self.previous_controller_history = []  # Issue 5: Retain context from previous controller
        self.previous_controller_name = None
        self.channel_histories = {}
        self.channel_controllers = {}
        self.active_channel_key = ""
        
        # Intercom target: 'ATC' (default) or 'CABIN'
        self.intercom_target = 'ATC'
        
        # Debug: Infinite Pattern Mode (prevents departure handoff)
        self.infinite_pattern = config.get('debug', {}).get('infinite_pattern', False)
        if self.infinite_pattern:
            print("LogicManager: ⚠️ INFINITE PATTERN MODE - No departure handoffs")
        
        # Track logging state (for Issue 5)
        self.last_position = None  # (lat, lon) for teleport detection
        
        # === 主动移交状态跟踪 ===
        self.handoff_triggered = {
            'departure': False,  # 起飞后移交离场
            'cruise': False,     # 巡航移交中心
            'approach': False    # 下降中移交进场
        }
        self.last_vs = 0  # 上一次垂直速度，用于判断爬升/下降
        self._was_on_ground = True  # Track ground→air transition for flight plan display

        # === FIR 跨区检测 ===
        self._current_fir: str | None = None   # 当前所在 FIR 代码
        self._fir_check_counter: int = 0       # 每 N 次遥测才检测一次（节省 CPU）
        self._FIR_CHECK_INTERVAL: int = 10     # 约每 10 秒检查一次（取决于遥测频率）
        try:
            from .fir_data import fir_detector as _fir_detector
            self._fir_detector = _fir_detector
            print("LogicManager: FIR detector loaded.")
        except Exception as e:
            self._fir_detector = None
            print(f"LogicManager: FIR detector unavailable: {e}")

        # Radar vector mode: when True, ATC heading/altitude/speed cards are
        # automatically applied to the simulator's autopilot.
        self.radar_vector_mode = False
        self._sim_bridge = None  # injected by app.py after SimBridge starts

        # Plugin manager reference (injected by app.py)
        self._plugin_manager = None
        
        # Defer log file creation to start() to avoid double initialization
        self.log_dir = os.environ.get("OPENFREQUENCY_LOG_DIR", "logs")
        self.log_file = None
        self.track_file = None
        self._logs_initialized = False

    def set_scheduler(self, scheduler):
        self.scheduler = scheduler
        print("LogicManager: Scheduler set.")

    def start(self):
        """
        Subscribes to events on the event bus.
        """
        # Issue 4: Initialize logs only once in start() to avoid Flask reloader double-init
        if not self._logs_initialized:
            self._init_logs()
            self._logs_initialized = True
        
        print("LogicManager: Subscribing to events...")
        event_bus.on('telemetry_update', self.on_telemetry_update)
        event_bus.on('atc_broadcast', self.on_atc_broadcast)
        event_bus.on('user_speech_recognized', self.on_user_speech)
        event_bus.on('llm_response_generated', self.on_llm_response)
        event_bus.on('sim_connection_status', self.on_sim_status)
        event_bus.on('flight_plan_loaded', self.on_flight_plan_loaded)
        event_bus.on('metar_fetch_request', self._handle_metar_fetch_request)
        event_bus.on('external_chat_log', self._handle_external_chat_log)
        event_bus.on('config_updated', self._handle_config_updated)
        # Auto-busy: keep workload_sim in sync with nearby traffic count
        event_bus.on('traffic_update', self._on_traffic_update)
        
        # Start Infinite Pattern Loop if enabled
        if self.infinite_pattern and self.scheduler:
            print("LogicManager: Scheduling Infinite Pattern check (10s interval)")
            self.scheduler.add_job(self._check_infinite_pattern, 'interval', seconds=10)
            
    def _check_infinite_pattern(self):
        """Automated flight loop for endurance testing."""
        if not self.infinite_pattern: return
        
        with context_lock:
            altitude = shared_context['aircraft'].get('altitude', 0)
            speed = shared_context['aircraft'].get('airspeed', 0)
            on_ground = shared_context['aircraft'].get('on_ground', False)
            # parking_brake = shared_context['aircraft'].get('parking_brake', False) # Need to add to context
            # Assuming speed < 1 and on_ground means parked for now
            
        # State Inference
        is_parked = on_ground and speed < 2
        is_flying = not on_ground and altitude > 500
        is_landed = on_ground and speed < 30
        
        # Logic
        # 1. Auto-Request Clearance if Parked for a while
        # Use a simple cooldown or random chance to avoid spam
        if is_parked:
            if random.random() < 0.3: # 30% chance every 10s
                print("InfinitePattern: Auto-requesting Clearance")
                # Simulate User Speech
                self.on_user_speech("Request IFR clearance to Shanghai")
                
        # 2. Reset if Landed (to allow loop to continue)
        # In a real sim, we can't easily "reset" position without SimConnect commands 
        # that write to the sim. For now, we just reset the internal state expectation
        # or maybe we can emit a 'reset_sim' event if SimBridge supports it.
        # Minimal viable: Just log it.
        if is_landed:
             print("InfinitePattern: Landed. Ready for next cycle.")
             # Theoretically we could trigger a helper to slew the aircraft back to parking
             
    def _init_logs(self):
        """Initialize log files - called only once from start()."""
        import os
        import datetime
        import glob
        
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)
            
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(self.log_dir, f"flight_log_{timestamp}.txt")
        self.track_file = os.path.join(self.log_dir, f"track_{timestamp}.csv")
        print(f"LogicManager: Logging to {self.log_file}")
        print(f"LogicManager: Track logging to {self.track_file}")
        
        # Restore history from latest log if recent (< 30 mins)
        try:
            files = glob.glob(os.path.join(self.log_dir, "flight_log_*.txt"))
            files.sort(key=os.path.getmtime)
            
            if files:
                last_log = files[-1]
                if time.time() - os.path.getmtime(last_log) < 1800:
                    print(f"LogicManager: Restoring history from {last_log}...")
                    with open(last_log, 'r', encoding='utf-8') as f:
                        lines = f.readlines()[-50:]
                        for line in lines:
                            line = line.strip()
                            if not line or line.startswith("---"): continue
                            b_idx = line.find("] ")
                            if b_idx != -1:
                                content = line[b_idx+2:]
                                s_idx = content.find(": ")
                                if s_idx != -1:
                                    sender = content[:s_idx]
                                    text = content[s_idx+2:]
                                    self.message_history.append({'sender': sender, 'text': text})
                    print(f"LogicManager: Restored {len(self.message_history)} messages.")
        except Exception as e:
            print(f"LogicManager: Failed to restore history: {e}")

        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write(f"--- OpenFrequency ATC Log Started: {timestamp} ---\n")
        
        # Start background task for METAR
        if self.scheduler:
            self.scheduler.add_job(self._update_metar, 'interval', minutes=10)
            # Fetch immediately on start
            import threading
            threading.Thread(target=self._update_metar, daemon=True).start()

    def _fetch_metar(self, icao):
        """Fetches real-world METAR from AviationWeather.gov using JSON API."""
        import requests
        try:
            url = f"https://aviationweather.gov/api/data/metar?ids={icao}&format=json"
            print(f"LogicManager: Fetching METAR for {icao} from {url}")
            resp = requests.get(url, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                if data and isinstance(data, list) and len(data) > 0:
                    metar_obj = data[0]
                    raw_text = metar_obj.get('rawOb', 'N/A')
                    # Parse interesting fields if needed, or just store raw
                    # Also get QNH from altim (mb? inHg? JSON has altim: 1015 (hPa))
                    # If altim > 800 it is hPa, otherwise inHg * 33.86? 
                    # Actually AviationWeather JSON 'altim' is usually hPa if from non-US, 
                    # but check units. 'altim': 1015. 
                    
                    with context_lock:
                        shared_context['environment']['metar'] = raw_text
                        shared_context['environment']['weather_data'] = metar_obj
                        
                        # Update QNH if available (convert to InHg for SimConnect if needed, or keep hPa)
                        # MSFS uses millibars/hPa usually or InHg. 
                        # Let's save both or trust the SimBridge to sync. 
                        # Actually, let's just make the AI aware of it.
                        
                    print(f"LogicManager: METAR updated: {raw_text}")
                    event_bus.emit('metar_updated', icao, raw_text, metar_obj)
                    return raw_text
            else:
                print(f"LogicManager: Fetch failed {resp.status_code}")
        except Exception as e:
            print(f"LogicManager: METAR fetch error: {e}")
        return None

    def _handle_metar_fetch_request(self, icao):
        if not icao or icao == 'N/A':
            return
        self._fetch_metar(icao)

    def _update_metar(self):
        """Called periodically to update weather."""
        # 1. Get Nearest Airport
        with context_lock:
            # For now logic_manager doesn't track position accurately enough to find nearest airport 
            # without a DB. But `environment` might have it if NavManager put it there.
            # Fallback: Use Origin or Destination from Flight Plan if nearest unknown.
            icao = shared_context['environment'].get('nearest_airport', 'N/A')
            
            if icao == 'N/A':
                # Try origin
                icao = shared_context['flight_plan'].get('origin', 'N/A')
            
            # If still N/A, try SimBrief last known? Or just skip.
            if icao == 'N/A' or len(icao) != 4:
                return

        self._fetch_metar(icao)

    def _broadcast_chat(self, sender, text):
        """Helper to send chat message and store in history."""
        msg_obj = {'sender': sender, 'text': text}
        
        # Store in history (Keep last 50)
        self.message_history.append(msg_obj)
        if len(self.message_history) > 50:
            self.message_history.pop(0)
            
        self.socketio.emit('chat_log', msg_obj)
        
        # Persist to disk
        try:
            import datetime
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {sender}: {text}\n")
        except Exception as e:
            print(f"LogicManager: Logging failed: {e}")

    def _handle_external_chat_log(self, sender, text):
        self._broadcast_chat(sender, text)

    def _handle_config_updated(self, new_config):
        self.config = new_config
        if self.ground_service:
            self.ground_service.update_config(new_config)
        # Re-read auto_busy setting when config changes
        imm = new_config.get('immersion', {})
        self.workload_sim.auto_busy  = imm.get('auto_busy_level', True)
        self.workload_sim.busy_level = imm.get('busy_level', 'medium')

    def _on_traffic_update(self, traffic_list):
        """Keep workload simulator in sync with real nearby aircraft count."""
        count = len(traffic_list) if isinstance(traffic_list, list) else 0
        self.workload_sim.update_traffic_count(count)
        # Emit effective busy level to dashboard so it can be shown in UI
        if self.socketio:
            self.socketio.emit('busy_level_update', {
                'count': count,
                'level': self.workload_sim.effective_busy_level,
                'auto': self.workload_sim.auto_busy,
            })

    def on_flight_plan_loaded(self, flight_plan):
        self._refresh_nearby_airports(force=True)
        # Push to dashboard immediately so it shows even before takeoff
        if flight_plan.get('destination', 'N/A') != 'N/A' or flight_plan.get('origin', 'N/A') != 'N/A':
            self.socketio.emit('flight_plan_update', flight_plan)

    def _refresh_nearby_airports(self, force=False):
        if not self.airport_frequency_service:
            return []

        with context_lock:
            aircraft = dict(shared_context.get('aircraft', {}))
            flight_plan = dict(shared_context.get('flight_plan', {}))
            existing = shared_context.get('environment', {}).get('nearby_airports', [])

        lat = aircraft.get('latitude')
        lon = aircraft.get('longitude')
        sqlite_path = self.config.get('navdata', {}).get('sqlite_path', '')

        airports = self.airport_frequency_service.get_nearby_airports(lat, lon, sqlite_path)

        # Fallback if nav DB is unavailable: include current flight plan airports if frequency data exists.
        if not airports:
            fallback_idents = []
            for key in ('origin', 'destination', 'alternate'):
                ident = (flight_plan.get(key) or '').strip().upper()
                if ident and ident not in fallback_idents:
                    fallback_idents.append(ident)

            for ident in fallback_idents:
                freqs = self.airport_frequency_service.get_airport_frequencies(ident)
                if freqs:
                    airports.append({
                        "ident": ident,
                        "name": ident,
                        "lat": None,
                        "lon": None,
                        "distance_nm": None,
                        "frequencies": freqs
                    })

        if force or airports != existing:
            with context_lock:
                shared_context['environment']['nearby_airports'] = airports
                if airports:
                    shared_context['environment']['current_airport'] = airports[0]['ident']
            self.socketio.emit('nearby_frequencies_update', {'airports': airports})
            self._refresh_ground_context(airports[0]['ident'] if airports else None)

        return airports

    def _refresh_ground_context(self, airport_ident=None):
        if not self.ground_service:
            return None

        with context_lock:
            aircraft = dict(shared_context.get('aircraft', {}))
            environment = dict(shared_context.get('environment', {}))
        airport_ident = (airport_ident or environment.get('current_airport') or environment.get('nearest_airport') or '').strip().upper()
        if not airport_ident or airport_ident == 'N/A':
            return None

        layout = self.ground_service.get_airport_layout(airport_ident)
        if not layout:
            return None

        taxiway_names = []
        for edge in layout.get('taxi_edges', []):
            if edge.get('kind') in {'taxiway', 'taxiway_link', 'apron_link'}:
                name = (edge.get('name') or '').strip()
                if name and name not in taxiway_names:
                    taxiway_names.append(name)

        stand_names = []
        for stand in layout.get('startup_locations', []):
            stand_name = (stand.get('gate_id') or stand.get('name') or '').strip()
            if stand_name and stand_name not in stand_names:
                stand_names.append(stand_name)

        route = None
        if self.taxi_router:
            preferred_runways = []
            wind_dir = aircraft.get('wind_dir')
            if self.airport_frequency_service:
                preferred_runways = self.airport_frequency_service.get_preferred_runways(airport_ident, wind_dir=wind_dir, limit=2)
            low_visibility = False
            weather_data = environment.get('weather_data') or {}
            vis = weather_data.get('visib') or weather_data.get('visibility')
            try:
                low_visibility = float(vis) < 5000
            except Exception:
                low_visibility = False
            aircraft_size = 'heavy' if self.config.get('user_profile', {}).get('heavy') else 'medium'
            route = self.taxi_router.suggest_taxi_route(
                airport_ident,
                {'lat': aircraft.get('latitude'), 'lon': aircraft.get('longitude')},
                preferred_runways=preferred_runways,
                aircraft_size=aircraft_size,
                low_visibility=low_visibility,
            )

        summary = {
            'airport_ident': airport_ident,
            'source': self.config.get('navdata', {}).get('ground_source', 'simulator'),
            'taxiway_names': taxiway_names[:40],
            'stand_names': stand_names[:80],
            'runway_count': len(layout.get('runways', [])),
            'startup_count': len(layout.get('startup_locations', [])),
            'taxi_node_count': len(layout.get('taxi_nodes', [])),
            'taxi_edge_count': len(layout.get('taxi_edges', [])),
            'suggested_taxi_route': route,
        }
        with context_lock:
            shared_context['navigation']['ground_layout_summary'] = summary
            shared_context['navigation']['current_taxi_path'] = route.get('taxiways', []) if route else []

        # Push suggested taxi route to the dashboard for visual highlighting
        if route and route.get('taxiways'):
            self.socketio.emit('suggested_taxi_route', {
                'airport_ident': airport_ident,
                'taxiways': route.get('taxiways', []),
                'target_runway': route.get('target_runway') or route.get('end_node', ''),
            })
        return summary

    def _format_channel_key(self, airport_ident, frequency_mhz, role):
        airport_ident = (airport_ident or 'AREA').strip().upper()
        role = (role or 'ATC').strip()
        return f"{airport_ident}:{float(frequency_mhz):.3f}:{role}"

    def _find_frequency_entry(self, frequency_mhz):
        airports = self._refresh_nearby_airports()
        if not airports:
            with context_lock:
                airports = list(shared_context.get('environment', {}).get('nearby_airports', []))

        try:
            frequency_mhz = round(float(frequency_mhz), 3)
        except Exception:
            return None

        best_match = None
        best_delta = 999.0
        for airport in airports:
            for entry in airport.get('frequencies', []):
                delta = abs(float(entry.get('frequency_mhz', 0.0)) - frequency_mhz)
                if delta < best_delta and delta <= 0.01:
                    best_delta = delta
                    best_match = {
                        "airport_ident": airport.get('ident'),
                        "airport_name": airport.get('name'),
                        "distance_nm": airport.get('distance_nm'),
                        "frequency_mhz": float(entry.get('frequency_mhz')),
                        "label": entry.get('label', ''),
                        "role": entry.get('role', 'ATC'),
                        "description": entry.get('description', '')
                    }
        return best_match

    def _restore_channel_history(self, channel_key):
        restored = list(self.channel_histories.get(channel_key, []))
        self.message_history = restored

    def switch_frequency_context(self, frequency_mhz, source='sim'):
        freq_entry = self._find_frequency_entry(frequency_mhz)
        current_controller = None
        duplicate_switch = False

        with context_lock:
            try:
                normalized_freq = round(float(frequency_mhz), 3)
            except Exception:
                return

            existing_frequency = round(float(shared_context['atc_state'].get('current_frequency', 0.0) or 0.0), 3)
            existing_channel_key = shared_context['atc_state'].get('current_channel_key', '')

            if self.active_channel_key:
                self.channel_histories[self.active_channel_key] = list(self.message_history)
                self.channel_controllers[self.active_channel_key] = shared_context['atc_state'].get('current_controller', 'ATC')

            if freq_entry:
                role = freq_entry['role']
                airport_ident = freq_entry['airport_ident']
                label = freq_entry['label']
                final_role = role if role in ["Center", "Emergency", "Unicom"] else f"{airport_ident} {role}"
                channel_key = self._format_channel_key(airport_ident, frequency_mhz, role)
                shared_context['environment']['current_airport'] = airport_ident
                shared_context['atc_state']['current_controller'] = final_role
                shared_context['atc_state']['current_frequency_label'] = label
                shared_context['atc_state']['current_frequency_role'] = role
                shared_context['atc_state']['current_channel_key'] = channel_key
            else:
                role = self._determine_controller(frequency_mhz, shared_context['aircraft'].get('altitude', 0))
                airport_ident = shared_context['environment'].get('nearest_airport', 'AREA')
                final_role = role if role in ["Center", "Emergency", "Unicom"] else f"{airport_ident} {role}"
                channel_key = self._format_channel_key(airport_ident, frequency_mhz, role)
                shared_context['atc_state']['current_controller'] = final_role
                shared_context['atc_state']['current_frequency_label'] = f"{final_role} {float(frequency_mhz):.3f}"
                shared_context['atc_state']['current_frequency_role'] = role
                shared_context['atc_state']['current_channel_key'] = channel_key

            duplicate_switch = (
                existing_frequency == normalized_freq and
                existing_channel_key == channel_key and
                source == 'sim'
            )

            shared_context['atc_state']['current_frequency'] = normalized_freq
            current_controller = shared_context['atc_state']['current_controller']
            self.active_channel_key = channel_key

        if duplicate_switch:
            self.last_freq = normalized_freq
            return

        self.socketio.emit('stop_active_audio', {'reason': 'frequency_change'})
        event_bus.emit('atis_stop')
        self._restore_channel_history(self.active_channel_key)
        self.previous_controller_name = self.channel_controllers.get(self.active_channel_key, current_controller)
        self._refresh_ground_context(freq_entry['airport_ident'] if freq_entry else airport_ident)
        self.socketio.emit('radio_context_changed', {
            'frequency': round(float(frequency_mhz), 3),
            'controller': current_controller,
            'channel_key': self.active_channel_key,
            'source': source
        })

        self._broadcast_chat("SYSTEM", f"Tuned: {float(frequency_mhz):.3f} ({current_controller})")
        self.last_freq = normalized_freq
        if "ATIS" in current_controller:
            self._broadcast_chat("SYSTEM", "--- ATIS Broadcast ---")
            airport_ident = freq_entry['airport_ident'] if freq_entry else None
            if airport_ident:
                event_bus.emit('atis_playback_request', airport_ident)
        elif not self.message_history:
            if "Emergency" in current_controller:
                self._broadcast_chat("SYSTEM", "--- Emergency Frequency 121.5 ---")
            elif "Unicom" not in current_controller:
                self._broadcast_chat("SYSTEM", "--- Switchboard: New Controller ---")
                event_bus.emit('proactive_atc_request', "pilot_tuned_new_frequency", shared_context)

    def _check_fir_crossing(self, lat: float, lon: float):
        """
        Detect when the aircraft crosses an FIR boundary at cruise altitude.
        Fires a proactive ATC handoff suggestion when the FIR changes.
        Only called while in CENTER phase above 10,000ft.
        """
        new_fir = self._fir_detector.get_current_fir(lat, lon)

        # First detection after takeoff — silently initialise, no handoff needed
        if self._current_fir is None:
            self._current_fir = new_fir
            if new_fir:
                print(f"LogicManager: FIR initialised → {new_fir}")
            return

        if new_fir and new_fir != self._current_fir:
            old_fir = self._current_fir
            self._current_fir = new_fir
            fir_info = self._fir_detector.get_fir_info(new_fir)
            fir_name = fir_info.get('name', new_fir) if fir_info else new_fir
            fir_freq = fir_info.get('center_freq') if fir_info else None
            print(f"LogicManager: ✈️ FIR crossing {old_fir} → {new_fir} ({fir_name})")

            # Update shared context so the LLM knows the new FIR
            with context_lock:
                shared_context['atc_state']['current_fir'] = new_fir
                shared_context['atc_state']['current_fir_name'] = fir_name
                if fir_freq:
                    shared_context['atc_state']['suggested_fir_freq'] = fir_freq

            event_bus.emit(
                'proactive_atc_request',
                'pilot_crossed_fir_boundary_suggest_center_handoff',
                shared_context,
            )

    def _determine_controller(self, freq, altitude=None):
        """Frequency map with emergency, ATIS, and altitude awareness."""
        f = float(freq)
        
        # Issue 6: Emergency frequency
        if 121.4 <= f <= 121.6:
            return "Emergency"
        
        # Issue 7: ATIS frequency range (typically 127-128 MHz)
        if 127.0 <= f <= 128.0:
            return "ATIS"
        
        # Standard frequencies
        if 121.6 <= f <= 121.95:
            return "Ground"
        elif 118.0 <= f <= 118.95:
            return "Tower"
        elif 118.95 < f < 119.0:
            return "Clearance Delivery"
        elif 122.8 == f:
            return "Unicom"
        elif 119.0 <= f <= 136.0:
            # Issue 1: Altitude-based determination
            if altitude and altitude > 18000:
                return "Center"
            return "Approach"
        return "Center"

    def _get_current_sender_name(self):
        """Returns current controller name for chat log."""
        with context_lock:
             return shared_context['atc_state'].get('current_controller', 'ATC')

    def on_telemetry_update(self, data):
        # data is the entire shared_context from SimBridge
        ac_data = data.get('aircraft', {})
        
        # Update shared context with latest data (keys are already standardized)
        with context_lock:
            shared_context['aircraft'].update(ac_data)
            
            # Broadcast to UI
            # Rate limit logs
            # print(f"LogicManager: Emit Telemetry: {ac_data['altitude']}ft") 
            try:
                self.socketio.emit('telemetry_update', shared_context['aircraft'])
            except Exception as e:
                print(f"LogicManager: Emit Error: {e}")
        
        # === Issue 5: Track Logging with Teleport Detection ===
        lat = ac_data.get('latitude')
        lon = ac_data.get('longitude')
        alt = ac_data.get('altitude')
        hdg = ac_data.get('heading')
        spd = ac_data.get('speed')
        
        if lat is not None and lon is not None:
            self._refresh_nearby_airports()
            with context_lock:
                current_controller = shared_context['atc_state'].get('current_controller', '')
            if 'Ground' in current_controller:
                self._refresh_ground_context()
            should_log = True
            is_teleport = False
            
            # Teleport detection: skip if >5nm from last position
            if self.last_position:
                from math import radians, sin, cos, sqrt, atan2
                # Haversine distance in nm
                lat1, lon1 = radians(self.last_position[0]), radians(self.last_position[1])
                lat2, lon2 = radians(lat), radians(lon)
                dlat, dlon = lat2 - lat1, lon2 - lon1
                a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
                c = 2 * atan2(sqrt(a), sqrt(1-a))
                distance_nm = 3440.065 * c  # Earth radius in nm
                
                if distance_nm > 5.0:
                    print(f"LogicManager: Teleport detected ({distance_nm:.1f}nm). Skipping track log.")
                    should_log = False
                    is_teleport = True
            
            # 关键修复：即使跳过也要更新位置，否则后续正常点也会被跳过
            self.last_position = (lat, lon)
            
            # 发送瞬移标记到前端，让前端也能正确处理
            if is_teleport:
                self.socketio.emit('teleport_detected', {'lat': lat, 'lon': lon})
            
            if should_log:
                try:
                    import datetime
                    ts = datetime.datetime.now().isoformat()
                    # Write to track file
                    with open(self.track_file, "a", encoding="utf-8") as f:
                        f.write(f"{ts},{lat},{lon},{alt},{hdg},{spd}\n")
                except Exception as e:
                    print(f"LogicManager: Track logging error: {e}")

            # 1. Frequency/Controller Handoff Check
            current_freq = ac_data.get('com1_freq')
            current_alt = ac_data.get('altitude', 0)
            try:
                current_freq = round(float(current_freq), 3) if current_freq else None
            except Exception:
                current_freq = None
            if current_freq and abs(current_freq - float(self.last_freq or 0.0)) >= 0.005:
                self.switch_frequency_context(current_freq, source='sim')

            # === 主动移交触发逻辑 ===
            vs = ac_data.get('vs', 0)  # 垂直速度 ft/min
            alt = ac_data.get('altitude', 0)
            on_ground = ac_data.get('on_ground', True)
            current_controller = shared_context['atc_state'].get('current_controller', '')

            # Detect ground→air transition: push flight plan to dashboard when airborne
            if self._was_on_ground and not on_ground:
                fp = shared_context.get('flight_plan', {})
                if fp.get('destination', 'N/A') != 'N/A' or fp.get('origin', 'N/A') != 'N/A':
                    self.socketio.emit('flight_plan_update', fp)
            self._was_on_ground = on_ground
            
            # 起飞后移交离场 (高度 > 1500ft, 爬升中, 未触发过)
            if (not on_ground and alt > 1500 and vs > 200 and 
                not self.handoff_triggered['departure'] and
                'Tower' in current_controller and
                not self.infinite_pattern):
                print(f"LogicManager: 🛫 主动移交触发 - 起飞爬升中，建议移交离场")
                self.handoff_triggered['departure'] = True
                event_bus.emit('proactive_atc_request', 
                              "pilot_climbing_after_takeoff_suggest_departure_handoff", 
                              shared_context)
            
            # 下降中移交进场 (高度 < 5000ft, 下降中, 未触发过)
            if (not on_ground and alt < 5000 and vs < -200 and alt > 500 and
                not self.handoff_triggered['approach'] and
                ('Center' in current_controller or 'Departure' in current_controller)):
                print(f"LogicManager: 🛬 主动移交触发 - 下降中，建议移交进场")
                self.handoff_triggered['approach'] = True
                event_bus.emit('proactive_atc_request', 
                              "pilot_descending_suggest_approach_handoff", 
                              shared_context)
            
            # 巡航移交中心 (高度 > FL180, 未触发过)
            if (not on_ground and alt > 18000 and abs(vs) < 500 and
                not self.handoff_triggered['cruise'] and
                'Departure' in current_controller):
                print(f"LogicManager: ✈️ 主动移交触发 - 巡航高度，建议移交中心")
                self.handoff_triggered['cruise'] = True
                event_bus.emit('proactive_atc_request', 
                              "pilot_at_cruise_altitude_suggest_center_handoff", 
                              shared_context)
            
            # FIR 跨区检测（仅在巡航高度 + Center 阶段执行，每10次遥测一次）
            if (not on_ground and alt > 10000 and 'Center' in current_controller
                    and self._fir_detector and lat is not None and lon is not None):
                self._fir_check_counter += 1
                if self._fir_check_counter >= self._FIR_CHECK_INTERVAL:
                    self._fir_check_counter = 0
                    self._check_fir_crossing(lat, lon)

            # 落地后重置移交状态
            if on_ground and ac_data.get('airspeed', 0) < 30:
                if any(self.handoff_triggered.values()):
                    print("LogicManager: 落地，重置主动移交状态")
                    self.handoff_triggered = {'departure': False, 'cruise': False, 'approach': False}
                self._current_fir = None  # 落地后重置 FIR，下次起飞重新检测
            
            self.last_vs = vs

    def on_atc_broadcast(self, message):
        """Handles ATC broadcasts from the immersion engine."""
        print(f"LogicManager: Broadcasting to UI: {message}")
        sender = self._get_current_sender_name()
        self._broadcast_chat(sender, message)
        # Optionally, trigger TTS for broadcasts
        event_bus.emit('tts_request', message)

    def on_user_speech(self, text):
        """Handles recognized speech from the user."""
        print(f"LogicManager: User speech received: '{text}'")
        self._broadcast_chat('Pilot', text)

        if self.intercom_target == 'CABIN':
            event_bus.emit('crew_message', {'text': text, 'target': 'all'})
            return
        
        # Issue 4: Check if ATC should ignore (too busy, didn't hear)
        if self.workload_sim.should_ignore():
            print(f"LogicManager: Workload very high. ATC ignored the call (silence).")
            # Delayed retry - pilot will call again
            delay = random.uniform(8, 15)
            if self.scheduler:
                self.scheduler.add_job(
                    func=self._prompt_retry,
                    args=[text],
                    trigger='date',
                    run_date=time.time() + delay
                )
            return
        
        if self.workload_sim.should_standby():
            with context_lock:
                callsign = shared_context['aircraft']['callsign']
            standby_text = f"{callsign}, standby."
            delay = random.uniform(3, 8)
            print(f"LogicManager: Workload high. Standing by for {delay:.1f} seconds.")
            sender = self._get_current_sender_name()
            self._broadcast_chat(sender, standby_text)
            event_bus.emit('tts_request', standby_text)
            
            # After a delay, re-process the original request
            if self.scheduler:
                self.scheduler.add_job(
                    func=self.process_llm_request, 
                    args=[text],
                    trigger='date',
                    run_date=time.time() + delay
                )
        else:
            self.process_llm_request(text)
    
    def _prompt_retry(self, original_text):
        """Called after ATC ignored. Prompts pilot to try again."""
        # Simulates "no response" scenario - in real life pilot would retry
        # For now, just log it. User can speak again.
        print(f"LogicManager: ATC ignored '{original_text}'. Pilot should try again.")

    def process_llm_request(self, text):
        """Sends the request to the LLM (or fast-tracks via quick reply template)."""
        print(f"LogicManager: Processing LLM request for '{text}'")
        with context_lock:
            current_controller = shared_context['atc_state'].get('current_controller', '')
            current_airport = shared_context['environment'].get('current_airport') or shared_context['environment'].get('nearest_airport')
            ctx_snapshot = dict(shared_context)

        if 'Ground' in current_controller:
            self._refresh_ground_context(current_airport)

        # ── Plugin hook: pilot input ─────────────────────────────────────────
        if self._plugin_manager:
            text = self._plugin_manager.hook_pilot_input(text) or text

        # ── Tier 1: Keyword / template auto-match ───────────────────────────
        # Pure readbacks / roger / wilco → instant canned response, no AI.
        stt_lang = self._config_audio_lang()
        qr_ctx = QuickReplyEngine.build_context_from_shared(ctx_snapshot)
        quick = QuickReplyEngine.auto_match(text, current_controller, qr_ctx, lang=stt_lang)
        if quick:
            print(f"LogicManager: [Tier 1] Template matched → '{quick[:60]}'")
            self.on_llm_response(quick, None)
            return

        # ── Tier 2: Lightweight / fast LLM ──────────────────────────────────
        # Try the fast model first.  If it signals ESCALATE, fall through to
        # the full thinking model (Tier 3).
        history = list(self.message_history)[-6:]
        fast_reply = self._try_fast_llm(text, ctx_snapshot, history)
        if fast_reply is not None:
            print(f"LogicManager: [Tier 2] Fast LLM answered → '{fast_reply[:60]}'")
            self.on_llm_response(fast_reply, None)
            return

        # ── Tier 3: Full / thinking LLM ─────────────────────────────────────
        print("LogicManager: [Tier 3] Escalating to full/thinking model.")
        event_bus.emit('llm_request', text, history)

    # ── Tier 2 helper ────────────────────────────────────────────────────────

    _FAST_ESCALATE_MARKER = 'ESCALATE'

    def _try_fast_llm(self, text: str, ctx_snapshot: dict, history: list):
        """
        Attempt to answer with the lightweight/fast LLM model.

        Returns:
            str  — fast-model ATC reply (use it directly)
            None — fast model signalled ESCALATE; caller should try Tier 3
        """
        llm = getattr(self, '_llm_client', None)
        if llm is None:
            return None

        # If fast and thinking model are identical, skip Tier 2 to avoid a
        # redundant call — Tier 3 will use the same model anyway.
        if getattr(llm, 'model_fast', None) == getattr(llm, 'model_thinking', None):
            return None

        controller = ctx_snapshot.get('atc_state', {}).get('current_controller', 'ATC')
        callsign   = ctx_snapshot.get('aircraft', {}).get('callsign', 'Station')
        airport    = (ctx_snapshot.get('environment', {}).get('current_airport') or
                      ctx_snapshot.get('environment', {}).get('nearest_airport') or 'unknown')
        alt        = ctx_snapshot.get('aircraft', {}).get('altitude', 0)
        phase      = ctx_snapshot.get('flight', {}).get('phase', 'unknown')

        system_prompt = (
            f"You are {controller} at {airport}. Aircraft callsign: {callsign}. "
            f"Phase: {phase}, Altitude: {alt} ft.\n"
            "Reply with ONE concise, standard ICAO ATC radio response. "
            "Output plain text only (no JSON). "
            f"If the request is unclear, complex, or requires detailed reasoning, "
            f"reply with exactly the single word: {self._FAST_ESCALATE_MARKER}"
        )

        try:
            reply = llm._call_llm_sync(
                system_prompt=system_prompt,
                user_message=text,
                max_tokens=120,
            )
            reply = (reply or '').strip()
            if not reply or reply.upper() == self._FAST_ESCALATE_MARKER:
                return None
            return reply
        except Exception as e:
            print(f"LogicManager: Tier 2 fast LLM error — {e}; escalating.")
            return None

    def _config_audio_lang(self) -> str:
        """Return 'en'/'zh'/'ja' for quick-reply template selection."""
        lang = self.config.get('audio', {}).get('stt_language', 'en')
        return lang if lang in ('en', 'zh', 'ja') else 'en'

    def handle_quick_reply(self, template_id: str, extra_vars: dict | None = None):
        """
        Called by the Flask socket handler when the UI sends a quick-reply
        template ID.  Renders the template and emits it as an ATC response
        without invoking the LLM.
        """
        with context_lock:
            ctx_snap = dict(shared_context)
        qr_ctx = QuickReplyEngine.build_context_from_shared(ctx_snap)
        if extra_vars:
            qr_ctx.update(extra_vars)
        lang = self._config_audio_lang()
        text = QuickReplyEngine.render(template_id, qr_ctx, lang=lang)
        if text:
            self.on_llm_response(text, None)

    def on_llm_response(self, text, action):
        """Handles the generated response from the LLM."""
        print(f"LogicManager: LLM response: '{text}' (Action: {action})")

        if not text or not text.strip():
            print("LogicManager: Received empty response (Silence).")
            return

        # ── Plugin hook: allow plugins to modify ATC text ─────────────────
        if self._plugin_manager:
            text = self._plugin_manager.hook_atc_response(text, action) or text

        sender = self._get_current_sender_name()
        self._broadcast_chat(sender, text)

        # ── Plugin hook: chat message logged ─────────────────────────────
        if self._plugin_manager:
            self._plugin_manager.hook_chat_message(sender, text)

        event_bus.emit('atc_instruction_issued', text, action, shared_context)
        self._emit_instruction_cards(text, sender)
        event_bus.emit('tts_request', text)

    def _emit_instruction_cards(self, text, sender):
        cards = InstructionExtractor.extract(text)
        if not cards:
            return
        self.socketio.emit('instruction_cards_update', {
            'sender': sender,
            'cards': cards,
            'source_text': text,
        })
        self._update_issued_instructions(cards, text)

        # ── Radar vector mode: apply AP commands to simulator ─────────────
        if self.radar_vector_mode and self._sim_bridge:
            self._apply_radar_vectors(cards)

    def _apply_radar_vectors(self, cards: list):
        """Push heading / altitude / speed cards to the simulator autopilot."""
        sb = self._sim_bridge
        for card in cards:
            t, v = card['type'], card['value']
            try:
                if t == 'HDG':
                    sb.set_autopilot_heading(float(v))
                elif t == 'ALT':
                    # v may be 'FL350', 'M840', or plain '15000'
                    alt_ft = self._parse_alt_to_feet(v)
                    if alt_ft is not None:
                        sb.set_autopilot_altitude(alt_ft)
                elif t == 'SPD':
                    sb.set_autopilot_speed(float(v))
            except Exception as e:
                print(f"LogicManager: radar vector apply error ({t}={v}): {e}")

    @staticmethod
    def _parse_alt_to_feet(value: str) -> float | None:
        """Convert FL350 / M840 / 15000 to feet."""
        import re
        v = str(value).strip().upper()
        m = re.match(r'^FL(\d+)$', v)
        if m:
            return float(m.group(1)) * 100
        m = re.match(r'^M(\d{3,5})$', v)
        if m:
            metres = float(m.group(1))
            if metres < 1000:
                metres *= 10          # M840 → 8400 m
            return metres * 3.28084
        try:
            return float(v)
        except ValueError:
            return None

    # Card-type → issued_instructions key mapping
    _CARD_TO_ISSUED = {
        'SQ':    'squawk',
        'ALT':   'cleared_altitude',
        'HDG':   'assigned_heading',
        'SPD':   'assigned_speed',
        'QNH':   'altimeter',
        'ALTIM': 'altimeter',
        'APP':   'approach_clearance',
        'TAXI':  'taxi_route',
    }

    def _update_issued_instructions(self, cards, raw_text):
        """Update shared_context issued_instructions from freshly extracted cards."""
        import re
        updates = {}
        for card in cards:
            key = self._CARD_TO_ISSUED.get(card['type'])
            if key:
                updates[key] = card['value']

        # Also try to detect departure runway / SID from clearance text
        raw_lower = raw_text.lower()
        rwy_m = re.search(r'\brunway\s+(\d{1,2}[lrc]?)\b', raw_lower)
        if rwy_m:
            updates['departure_runway'] = rwy_m.group(1).upper()

        sid_m = re.search(r'\bvia\s+([A-Z]{2,6}\d[A-Z]?)\b', raw_text, re.IGNORECASE)
        if sid_m:
            updates['sid'] = sid_m.group(1).upper()

        if not updates:
            return
        with context_lock:
            issued = shared_context['atc_state'].setdefault('issued_instructions', {})
            issued.update(updates)
            # Keep last_instruction in sync for legacy consumers
            shared_context['atc_state']['last_instruction'] = raw_text

    def on_sim_status(self, data):
        """Handles sim connection status updates."""
        # data = {'connected': bool, 'msg': str}
        self.socketio.emit('sim_status', data)
