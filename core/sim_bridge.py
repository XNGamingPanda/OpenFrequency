import threading
import time
import copy
import math
import random

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
        
        # Mock mode for debugging without MSFS
        debug_cfg = config.get('debug', {})
        self.mock_mode = debug_cfg.get('mock_mode', False)
        self.mock_data = debug_cfg.get('mock_data', {})
        
        if self.mock_mode:
            print("SimBridge: ⚠️ MOCK MODE ENABLED - No real SimConnect connection")
        else:
            print("SimBridge: Initialized.")

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _connect(self):
        if self.mock_mode:
            self.connected = True
            print("SimBridge: Mock connection established")
            self.bus.emit('sim_connection_status', {'connected': True, 'msg': 'Mock Mode Active'})
            return True
            
        try:
            from SimConnect import SimConnect, AircraftRequests
            self.sm = SimConnect()
            self.aq = AircraftRequests(self.sm, _time=2000)
            self.connected = True
            print("SimBridge: Connected to MSFS/FSX successfully!")
            self.bus.emit('sim_connection_status', {'connected': True, 'msg': 'Connected to Simulator'})
            return True
        except Exception:
            self.connected = False
            return False

    def _get_mock_telemetry(self):
        """Generate mock telemetry data for debugging."""
        t = time.time()
        
        # Check for infinite pattern mode
        infinite_pattern = self.config.get('debug', {}).get('infinite_pattern', False)
        
        # Default mock values with optional overrides
        base_lat = self.mock_data.get('latitude', 40.08)
        base_lon = self.mock_data.get('longitude', 116.58)
        base_freq = self.mock_data.get('com1_freq', 118.1)
        
        if infinite_pattern:
            # Simulate traffic pattern at 1000-1500ft AGL
            # Pattern phases: Takeoff -> Crosswind -> Downwind -> Base -> Final
            cycle_time = 120  # 2 minutes per circuit
            phase = (t % cycle_time) / cycle_time  # 0.0 - 1.0
            
            # Circuit path (rectangular pattern)
            pattern_radius = 0.02  # ~2km
            
            if phase < 0.2:  # Takeoff/Departure (climbing)
                angle = phase * 5 * 0.5 * math.pi  # 0 to 90 degrees
                alt = 0 + phase * 5 * 1500  # 0 to 1500ft
                on_ground = phase < 0.02
                hdg = 360  # Runway heading
            elif phase < 0.4:  # Crosswind turn
                angle = 0.5 * math.pi + (phase - 0.2) * 5 * 0.5 * math.pi
                alt = 1500
                on_ground = False
                hdg = 90
            elif phase < 0.6:  # Downwind
                angle = math.pi + (phase - 0.4) * 5 * 0.5 * math.pi
                alt = 1500
                on_ground = False
                hdg = 180
            elif phase < 0.8:  # Base turn (descending)
                angle = 1.5 * math.pi + (phase - 0.6) * 5 * 0.5 * math.pi
                alt = 1500 - (phase - 0.6) * 5 * 500  # 1500 to 1000ft
                on_ground = False
                hdg = 270
            else:  # Final approach (descending)
                angle = 0
                progress = (phase - 0.8) * 5  # 0 to 1
                alt = 1000 - progress * 1000  # 1000 to 0ft
                on_ground = progress > 0.95
                hdg = 360
            
            lat = base_lat + math.cos(angle) * pattern_radius * 0.5
            lon = base_lon + math.sin(angle) * pattern_radius
            spd = 120 if on_ground else 100  # Pattern speed
            flaps = 0.3 if phase > 0.7 else 0  # Flaps on final
            gear = 1 if phase > 0.6 else 0
        else:
            # Normal cruise mode
            base_alt = self.mock_data.get('altitude', 10000)
            base_hdg = self.mock_data.get('heading', 270)
            base_spd = self.mock_data.get('airspeed', 250)
            
            # Force auto movement for now to ensure user sees changes in debug mode
            auto_move = self.mock_data.get('auto_movement', True)
            
            if auto_move:
                lat = base_lat + math.sin(t * 0.05) * 0.02
                lon = base_lon + math.cos(t * 0.05) * 0.02
                alt = base_alt + math.sin(t * 0.05) * 100
                hdg = (base_hdg + t * 5.0) % 360  # Faster heading change
                spd = base_spd + random.uniform(-10, 10)
            else:
                lat, lon, alt, hdg, spd = base_lat, base_lon, base_alt, base_hdg, base_spd
            
            on_ground = self.mock_data.get('on_ground', False)
            flaps = self.mock_data.get('flaps', 0)
            gear = 0
        
        return {
            'latitude': lat,
            'longitude': lon,
            'altitude': alt,
            'heading': hdg,
            'airspeed': spd,
            'com1_freq': base_freq,
            'g_force': 1.0 + random.uniform(-0.1, 0.1),
            'on_ground': on_ground,
            'throttle': self.mock_data.get('throttle', 0.7),
            'flaps': flaps,
            'gear': gear,
            'n1': 85 if not on_ground else 30,
            'vs': 0 if on_ground else random.uniform(-500, 500),
            'pitch': random.uniform(-5, 5),
            'bank': random.uniform(-15, 15)
        }

    def _loop(self):
        print("SimBridge: Thread started...")
        
        if self.mock_mode:
            print("SimBridge: Running in MOCK MODE")
        else:
            print("SimBridge: Attempting to connect to MSFS...")
        
        while not self._stop_event.is_set():
            if not self.connected:
                if self._connect():
                    time.sleep(1)
                else:
                    self.bus.emit('sim_connection_status', {'connected': False, 'msg': 'Searching for MSFS...'})
                    time.sleep(2)
                    continue
            
            try:
                # === MOCK MODE ===
                if self.mock_mode:
                    data = self._get_mock_telemetry()
                    context_update = data
                else:
                    # === REAL SIMCONNECT ===
                    if self.aq is None:
                        raise ConnectionError("AircraftRequests object (self.aq) is None")
                        
                    lat = self.aq.get("PLANE_LATITUDE")
                    lon = self.aq.get("PLANE_LONGITUDE")
                    alt = self.aq.get("PLANE_ALTITUDE")
                    hdg_raw = self.aq.get("PLANE_HEADING_DEGREES_MAGNETIC")
                    hdg = math.degrees(hdg_raw) if hdg_raw is not None else 0
                    if hdg < 0: hdg += 360
                    spd = self.aq.get("AIRSPEED_INDICATED")
                    com1 = self.aq.get("COM_ACTIVE_FREQUENCY:1")
                    
                    g_force = self.aq.get("ACCELERATION_BODY_Z") or 1.0
                    on_ground = bool(self.aq.get("SIM_ON_GROUND"))
                    throttle = self.aq.get("GENERAL_ENG_THROTTLE_LEVER_POSITION:1") or 0
                    flaps = self.aq.get("FLAPS_HANDLE_PERCENT") or 0
                    
                    # Extended logic (Fix for User Issue 4: Invalid IDs)
                    # ENG_N1_RPM is not standard. TURB_ENG_N1 is the correct SimVar for N1%.
                    n1 = self.aq.get("TURB_ENG_N1:1") or 0 
                    egt = self.aq.get("ENG_EXHAUST_GAS_TEMPERATURE:1") or 0
                    vs = self.aq.get("VERTICAL_SPEED") or 0
                    pitch = self.aq.get("PLANE_PITCH_DEGREES") or 0
                    bank = self.aq.get("PLANE_BANK_DEGREES") or 0
                    wind_dir = self.aq.get("AMBIENT_WIND_DIRECTION") or 0
                    wind_spd = self.aq.get("AMBIENT_WIND_VELOCITY") or 0
                    fuel_flow = self.aq.get("ENG_FUEL_FLOW_PPH:1") or 0
                    parking_brake = bool(self.aq.get("BRAKE_PARKING_POSITION"))
                    combustion = bool(self.aq.get("GENERAL_ENG_COMBUSTION:1"))
                    gear = self.aq.get("GEAR_HANDLE_POSITION") or 0

                    context_update = {
                        'latitude': lat if lat is not None else 40.08,
                        'longitude': lon if lon is not None else 116.58,
                        'altitude': alt if alt is not None else 0,
                        'heading': hdg,
                        'airspeed': spd if spd is not None else 0,
                        'com1_freq': com1 if com1 is not None else 118.1,
                        'g_force': g_force,
                        'on_ground': on_ground,
                        'throttle': throttle,
                        'flaps': flaps,
                        'n1': n1,
                        'egt': egt,
                        'vs': vs,
                        'pitch': math.degrees(pitch) if pitch is not None else 0,
                        'bank': math.degrees(bank) if bank is not None else 0,
                        'wind_dir': wind_dir,
                        'wind_spd': wind_spd,
                        'fuel_flow': fuel_flow,
                        'parking_brake': parking_brake,
                        'combustion': combustion,
                        'gear': gear
                    }

                # Update Context
                with self.lock:
                    for key, val in context_update.items():
                        self.context['aircraft'][key] = val
                    context_copy = copy.deepcopy(self.context)
                
                self.bus.emit('telemetry_update', context_copy)
                time.sleep(0.1) # 10Hz update rate
                
            except Exception as e:
                self.connected = False
                self.bus.emit('sim_connection_status', {'connected': False, 'msg': 'Connection Lost'})
                time.sleep(2)
        
        print("SimBridge: Thread stopped.")