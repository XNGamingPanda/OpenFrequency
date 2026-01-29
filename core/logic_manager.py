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
        
        # Initialize Logging
        import os
        import datetime
        self.log_dir = "logs"
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)
            
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(self.log_dir, f"flight_log_{timestamp}.txt")
        print(f"LogicManager: Logging to {self.log_file}")
        
        # Restore history from latest log if recent (< 30 mins)
        try:
            import glob
            files = glob.glob(os.path.join(self.log_dir, "flight_log_*.txt"))
            # Filter out the one we just created? it's handled by finding existing ones before... 
            # Actually we just bonded self.log_file name but haven't written yet.
            # But glob finds files ON DISK.
            
            # Sort by time
            files.sort(key=os.path.getmtime)
            
            if files:
                last_log = files[-1]
                # Check age
                if time.time() - os.path.getmtime(last_log) < 1800:
                    print(f"LogicManager: Restoring history from {last_log}...")
                    with open(last_log, 'r', encoding='utf-8') as f:
                        lines = f.readlines()[-50:] # Last 50 lines
                        for line in lines:
                            line = line.strip()
                            if not line or line.startswith("---"): continue
                            
                            # Expected format: [HH:MM:SS] Sender: Text
                            # 1. Find end of timestamp
                            b_idx = line.find("] ")
                            if b_idx != -1:
                                # content = "Sender: Text"
                                content = line[b_idx+2:]
                                # 2. Find separator between Sender and Text
                                s_idx = content.find(": ")
                                if s_idx != -1:
                                    sender = content[:s_idx]
                                    text = content[s_idx+2:]
                                    self.message_history.append({'sender': sender, 'text': text})
                    print(f"LogicManager: Restored {len(self.message_history)} messages.")
        except Exception as e:
            print(f"LogicManager: Failed to restore history: {e}")

        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write(f"--- OpenSky ATC Log Started: {timestamp} (Continuation) ---\n")

    def set_scheduler(self, scheduler):
        self.scheduler = scheduler
        print("LogicManager: Scheduler set.")

    def start(self):
        """
        Subscribes to events on the event bus.
        """
        print("LogicManager: Subscribing to events...")
        event_bus.on('telemetry_update', self.on_telemetry_update)
        event_bus.on('atc_broadcast', self.on_atc_broadcast)
        event_bus.on('user_speech_recognized', self.on_user_speech)
        event_bus.on('llm_response_generated', self.on_llm_response)
        event_bus.on('sim_connection_status', self.on_sim_status)
        
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

    def _determine_controller(self, freq):
        """Simple frequency map for demo/fallback."""
        # Check integer part
        f = float(freq)
        if 121.6 <= f <= 121.95:
            return "Ground"
        elif 118.0 <= f <= 118.95:
            return "Tower"
        elif 122.8 == f:
            return "Unicom"
        elif 119.0 <= f <= 136.0:
            return "Approach/Departure" # Generic
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

            # 1. Frequency/Controller Handoff Check
            current_freq = ac_data.get('com1_freq')
            if current_freq and current_freq != self.last_freq:
                new_controller = self._determine_controller(current_freq)
                
                if new_controller != shared_context['atc_state']['current_controller']:
                    # Try to get location context
                    icao = shared_context['environment'].get('nearest_airport', 'N/A')
                    if icao == 'N/A' or len(icao) != 4:
                        icao = shared_context['flight_plan'].get('origin', '')
                    
                    final_role = new_controller
                    if icao and len(icao) == 4 and new_controller not in ["Center", "Control"]:
                        final_role = f"{icao} {new_controller}"
                    
                    shared_context['atc_state']['current_controller'] = final_role
                    
                    # --- SWITCHBOARD MODEL: AMNESIA ---
                    # Clear history to prevent context bleeding
                    self.message_history.clear()
                    print("LogicManager: Context cleared due to frequency change.")
                    
                    msg = f"Tuned: {current_freq} ({final_role})"
                    self._broadcast_chat("SYSTEM", msg)
                    self._broadcast_chat("SYSTEM", "--- Switchboard: Context Reset ---")
                    
                    # PROACTIVE TRIGGER: If switching to a valid station (not Unicom), ATC greets you.
                    # We send a signal to LLM to introduce itself.
                    if new_controller != "Unicom":
                        # We use a timer or direct call? Direct call for now, but usually should wait for pilot check-in.
                        # User requested "Proactive", so maybe "Station Calling" logic.
                        # For now, let's just log it. To make it speak, we need to invoke llm_client.
                        # We need access to llm_client here. It's usually decoupled via EventBus.
                        # We emit a 'request_proactive' event.
                        print(f"LogicManager: Triggering Proactive Greeting for {new_controller}")
                        event_bus.emit('proactive_atc_request', "pilot_tuned_new_frequency", shared_context)

                self.last_freq = current_freq

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
        
        if self.workload_sim.should_standby():
            standby_text = f"{shared_context['aircraft']['callsign']}, standby."
            delay = random.uniform(3, 8)
            print(f"LogicManager: Workload high. Standing by for {delay:.1f} seconds.")
            sender = self._get_current_sender_name()
            self._broadcast_chat(sender, standby_text)
            event_bus.emit('tts_request', standby_text)
            
            # After a delay, re-process the original request
            self.scheduler.add_job(
                func=self.process_llm_request, 
                args=[text],
                trigger='date',
                run_date=time.time() + delay
            )
        else:
            self.process_llm_request(text)

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