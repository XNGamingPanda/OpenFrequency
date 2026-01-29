import threading
import time
import copy
from SimConnect import SimConnect, AircraftRequests

class SimBridge:
    def __init__(self, config, context, lock, bus):
        self.config = config
        self.context = context
        self.lock = lock
        self.bus = bus
        self.connected = False
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self.sm = None
        self.aq = None
        print("SimBridge: Initialized.")

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _connect(self):
        try:
            self.sm = SimConnect()
            self.aq = AircraftRequests(self.sm, _time=2000)
            self.connected = True
            print("SimBridge: Connected to MSFS/FSX successfully!")
            self.bus.emit('sim_connection_status', {'connected': True, 'msg': 'Connected to Simulator'})
            return True
        except Exception:
            self.connected = False
            return False

    def _loop(self):
        print("SimBridge: Thread started, attempting to connect to MSFS...")
        
        while not self._stop_event.is_set():
            if not self.connected:
                if self._connect():
                    time.sleep(1)
                else:
                    # Retry every 2 seconds if not connected
                    self.bus.emit('sim_connection_status', {'connected': False, 'msg': 'Searching for MSFS...'})
                    time.sleep(2)
                    continue
            
            try:
                # Fetch Data
                # Note: AircraftRequests fetches data when properties are accessed
                lat = self.aq.get("PLANE_LATITUDE")
                lon = self.aq.get("PLANE_LONGITUDE")
                alt = self.aq.get("PLANE_ALTITUDE")
                if not hasattr(self, '_math_imported'):
                    import math
                    self._math = math
                    self._math_imported = True

                lat = self.aq.get("PLANE_LATITUDE")
                lon = self.aq.get("PLANE_LONGITUDE")
                alt = self.aq.get("PLANE_ALTITUDE")
                hdg_raw = self.aq.get("PLANE_HEADING_DEGREES_MAGNETIC")
                # Fix: SimConnect python wrapper often returns radians even if variable name says degrees
                hdg = self._math.degrees(hdg_raw) if hdg_raw is not None else 0
                if hdg < 0: hdg += 360 # Ensure 0-360 range
                spd = self.aq.get("AIRSPEED_INDICATED")
                com1 = self.aq.get("COM_ACTIVE_FREQUENCY:1")
                
                # Debug logging every 5 seconds
                # if not hasattr(self, '_last_debug'): self._last_debug = 0
                # if time.time() - self._last_debug > 5:
                #    print(f"SimBridge Debug: Lat={lat}, Lon={lon}, Alt={alt}, Hdg={hdg}, Freq={com1}")
                #    self._last_debug = time.time()
                
                # Check for None (data not ready yet)
                if lat is None or lon is None:
                    # Watchdog: If data remains None for too long, force reconnect
                    if not hasattr(self, '_none_data_start') or self._none_data_start is None: 
                        self._none_data_start = time.time()
                    
                    if time.time() - self._none_data_start > 10:
                        print("SimBridge: Watchdog triggered - No data for 10s. Forcing reconnect...")
                        raise ConnectionError("No data received for 10s")
                        
                    time.sleep(0.1)
                    continue
                else:
                    self._none_data_start = None # Reset watchdog

                with self.lock:
                    self.context['aircraft']['latitude'] = lat
                    self.context['aircraft']['longitude'] = lon
                    self.context['aircraft']['altitude'] = alt
                    self.context['aircraft']['heading'] = hdg
                    self.context['aircraft']['airspeed'] = spd
                    self.context['aircraft']['com1_freq'] = com1
                    
                    # Make a deepcopy to avoid race conditions in listeners
                    context_copy = copy.deepcopy(self.context)

                # Emit event
                self.bus.emit('telemetry_update', context_copy)
                
                # Rate limit (approx 20Hz is overkill for SimConnect usually, 10Hz is fine)
                time.sleep(0.1)

            except Exception as e:
                print(f"SimBridge Error (Connection Lost?): {e}")
                self.connected = False
                try: 
                    if self.sm: self.sm.quit()
                except: pass
                self.sm = None
                self.aq = None
                self._none_data_start = None
                
                # Notify UI
                self.bus.emit('sim_connection_status', {'connected': False, 'msg': 'Connection Lost (Retrying...)'})
                
                time.sleep(2)
        
        print("SimBridge: Thread stopped.")