import threading
import time
import random
from .context import shared_context, context_lock, event_bus
from .immersion.workload_sim import WorkloadSimulator

class LogicManager:
    """
    The central coordinator. Does not own other modules.
    It subscribes to events on the EventBus and emits data to the UI via SocketIO.
    """
    def __init__(self, config, socketio):
        self.config = config
        self.socketio = socketio
        self.workload_sim = WorkloadSimulator(config)
        self.scheduler = None
        self.last_freq = 0.0
        self.message_history = [] # Buffer for chat log
        self.previous_controller_history = []  # Issue 5: Retain context from previous controller
        self.previous_controller_name = None
        
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
        
        # Defer log file creation to start() to avoid double initialization
        self.log_dir = "logs"
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
            f.write(f"--- OpenSky ATC Log Started: {timestamp} ---\n")
        
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
                    return raw_text
            else:
                print(f"LogicManager: Fetch failed {resp.status_code}")
        except Exception as e:
            print(f"LogicManager: METAR fetch error: {e}")
        return None

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
        elif 122.8 == f:
            return "Unicom"
        elif 119.0 <= f <= 136.0:
            # Issue 1: Altitude-based determination
            if altitude and altitude > 18000:
                return "Center"
            return "Approach/Departure"
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
            if current_freq and current_freq != self.last_freq:
                new_controller = self._determine_controller(current_freq, current_alt)
                
                if new_controller != shared_context['atc_state']['current_controller']:
                    # Try to get location context
                    icao = shared_context['environment'].get('nearest_airport', 'N/A')
                    if icao == 'N/A' or len(icao) != 4:
                        icao = shared_context['flight_plan'].get('origin', '')
                    
                    final_role = new_controller
                    if icao and len(icao) == 4 and new_controller not in ["Center", "Control", "Emergency", "ATIS"]:
                        final_role = f"{icao} {new_controller}"
                    
                    # Issue 5: Save previous context before clearing
                    if self.message_history:
                        self.previous_controller_name = shared_context['atc_state'].get('current_controller', 'Previous ATC')
                        self.previous_controller_history = list(self.message_history)[-10:]  # Keep last 10
                    
                    shared_context['atc_state']['current_controller'] = final_role
                    
                    # Clear current history but keep previous controller reference
                    self.message_history.clear()
                    print(f"LogicManager: Context switched. Previous controller: {self.previous_controller_name}")
                    
                    msg = f"Tuned: {current_freq} ({final_role})"
                    self._broadcast_chat("SYSTEM", msg)
                    
                    # Issue 7: Handle ATIS specially
                    if new_controller == "ATIS":
                        self._broadcast_chat("SYSTEM", "--- ATIS Broadcast ---")
                        event_bus.emit('atis_playback_request', icao)
                    elif new_controller == "Emergency":
                        self._broadcast_chat("SYSTEM", "--- Emergency Frequency 121.5 ---")
                    else:
                        self._broadcast_chat("SYSTEM", "--- Switchboard: New Controller ---")
                        # PROACTIVE TRIGGER
                        if new_controller != "Unicom":
                            print(f"LogicManager: Triggering Proactive Greeting for {new_controller}")
                            event_bus.emit('proactive_atc_request', "pilot_tuned_new_frequency", shared_context)

                self.last_freq = current_freq

            # === 主动移交触发逻辑 ===
            vs = ac_data.get('vs', 0)  # 垂直速度 ft/min
            alt = ac_data.get('altitude', 0)
            on_ground = ac_data.get('on_ground', True)
            current_controller = shared_context['atc_state'].get('current_controller', '')
            
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
            
            # 落地后重置移交状态
            if on_ground and ac_data.get('airspeed', 0) < 30:
                if any(self.handoff_triggered.values()):
                    print("LogicManager: 落地，重置主动移交状态")
                    self.handoff_triggered = {'departure': False, 'cruise': False, 'approach': False}
            
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
        """Sends the request to the LLM."""
        print(f"LogicManager: Processing LLM request for '{text}'")
        # Pass recent history (exclude the very last one if it is the current message to avoid duplication in prompt, 
        # but simpler to just pass last 10 and let LLMClient handle formatting)
        # actually, let's just pass the last 6 messages for context
        history = list(self.message_history)[-6:]
        event_bus.emit('llm_request', text, history)

    def on_llm_response(self, text, action):
        """Handles the generated response from the LLM."""
        print(f"LogicManager: LLM response: '{text}' (Action: {action})")
        
        # If LLM decides to be silent (e.g. implied readback confirmation), skip broadcast
        if not text or not text.strip():
            print("LogicManager: Received empty response (Silence).")
            return

        sender = self._get_current_sender_name()
        self._broadcast_chat(sender, text)
        event_bus.emit('tts_request', text)

    def on_sim_status(self, data):
        """Handles sim connection status updates."""
        # data = {'connected': bool, 'msg': str}
        self.socketio.emit('sim_status', data)