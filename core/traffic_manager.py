"""
Traffic Awareness System - TrafficStateManager
Monitors FSLTL AI traffic via SimConnect and detects state changes.
"""
import threading
import time
import math
import hashlib
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Dict, Optional, Any
from .context import event_bus, shared_context, context_lock

class TrafficState(Enum):
    """State machine states for AI aircraft."""
    UNKNOWN = auto()
    PARKED = auto()
    PUSHBACK = auto()
    TAXIING = auto()
    TAKEOFF_ROLL = auto()
    AIRBORNE = auto()
    APPROACH = auto()
    LANDING = auto()
    VACATING = auto()

@dataclass
class AircraftTrackingData:
    """Tracking data for a single AI aircraft."""
    callsign: str
    state: TrafficState = TrafficState.UNKNOWN
    pending_state: Optional[TrafficState] = None  # State waiting for hysteresis confirmation
    pending_state_start: float = 0.0  # When pending state was first detected
    
    # Telemetry
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    heading: float = 0.0
    airspeed: float = 0.0
    vertical_speed: float = 0.0
    on_ground: bool = True
    
    # Previous frame data (for change detection)
    prev_latitude: float = 0.0
    prev_longitude: float = 0.0
    prev_heading: float = 0.0
    prev_on_ground: bool = True
    
    # Metadata
    last_seen: float = field(default_factory=time.time)
    voice_id: Optional[str] = None  # Assigned TTS voice

class TrafficStateManager:
    """
    Manages tracking and state detection for AI traffic.
    Scans SimConnect for AI objects and emits events on state changes.
    """
    
    # Hysteresis threshold - state must persist for this long before confirmation
    HYSTERESIS_SECONDS = 2.0
    
    # Teleport detection threshold (nautical miles)
    TELEPORT_THRESHOLD_NM = 5.0
    
    # Stale aircraft cleanup time (seconds without update)
    STALE_TIMEOUT = 30.0
    
    # Scan interval
    SCAN_INTERVAL = 0.5  # 2 Hz
    
    def __init__(self, config, sim_bridge):
        self.config = config
        self.sim_bridge = sim_bridge  # Need access to SimConnect instance
        self.aircraft: Dict[str, AircraftTrackingData] = {}
        self.lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self.enabled = config.get('traffic', {}).get('enabled', True)
        
        print("TrafficStateManager: Initialized.")
    
    def start(self):
        if self.enabled:
            self._thread.start()
            print("TrafficStateManager: Scanning thread started.")
        else:
            print("TrafficStateManager: Disabled in config.")
    
    def stop(self):
        self._stop_event.set()
    
    def _loop(self):
        """Main scanning loop."""
        while not self._stop_event.is_set():
            if self.sim_bridge and self.sim_bridge.connected:
                try:
                    self._scan_traffic()
                    self._cleanup_stale()
                except Exception as e:
                    print(f"TrafficStateManager Error: {e}")
            
            time.sleep(self.SCAN_INTERVAL)
    
    def _scan_traffic(self):
        """Scan SimConnect for AI aircraft."""
        # Note: The Python SimConnect library's approach to iterating AI objects
        # requires using simconnect.get_info() or iterating simobjects.
        # This implementation assumes we can access AI data.
        
        sm = self.sim_bridge.sm
        if not sm:
            return
        
        # Get player position for radius filtering
        with context_lock:
            player_lat = shared_context['aircraft'].get('latitude', 0)
            player_lon = shared_context['aircraft'].get('longitude', 0)
        
        try:
            # Use SimConnect to iterate AI objects
            # Note: Python-SimConnect may have limited AI object support
            # We'll use SIMCONNECT_OBJECT_ID_USER + iteration or RequestDataOnSimObject
            
            # For now, we'll use a mock approach that would be replaced
            # with actual SimConnect AI enumeration when the library supports it
            self._scan_ai_objects(sm, player_lat, player_lon)
            
        except Exception as e:
            print(f"TrafficStateManager: SimConnect scan error: {e}")
    
    def _scan_ai_objects(self, sm, player_lat, player_lon):
        """
        Scan AI objects from SimConnect.
        This is a placeholder - actual implementation depends on SimConnect library capabilities.
        """
        # The Python-SimConnect library has limited support for AI objects.
        # Full implementation would require:
        # 1. Using sm.get_next_dispatch() to handle SIMCONNECT_RECV_SIMOBJECT_DATA
        # 2. Or using pySimConnect's internal iteration if supported
        
        # For demonstration, we emit a debug message periodically
        if not hasattr(self, '_last_scan_log') or time.time() - self._last_scan_log > 30:
            self._last_scan_log = time.time()
            # print("TrafficStateManager: Periodic scan running (AI object enumeration pending implementation)")
        
        pass  # Will be implemented when SimConnect AI support is confirmed
    
    def update_aircraft(self, callsign: str, data: Dict[str, Any]):
        """
        Update tracking data for an aircraft and detect state changes.
        Called when new telemetry is received (from scan or external source).
        """
        with self.lock:
            if callsign not in self.aircraft:
                # New aircraft detected
                self.aircraft[callsign] = AircraftTrackingData(
                    callsign=callsign,
                    voice_id=self._assign_voice(callsign)
                )
                print(f"TrafficStateManager: New aircraft detected: {callsign}")
            
            ac = self.aircraft[callsign]
            
            # Store previous frame data
            ac.prev_latitude = ac.latitude
            ac.prev_longitude = ac.longitude
            ac.prev_heading = ac.heading
            ac.prev_on_ground = ac.on_ground
            
            # Update current data
            ac.latitude = data.get('latitude', ac.latitude)
            ac.longitude = data.get('longitude', ac.longitude)
            ac.altitude = data.get('altitude', ac.altitude)
            ac.heading = data.get('heading', ac.heading)
            ac.airspeed = data.get('airspeed', ac.airspeed)
            ac.vertical_speed = data.get('vertical_speed', ac.vertical_speed)
            ac.on_ground = data.get('on_ground', ac.on_ground)
            ac.last_seen = time.time()
            
            # Check for teleport
            if self._check_teleport(ac):
                print(f"TrafficStateManager: {callsign} teleported - resetting state")
                ac.state = TrafficState.UNKNOWN
                ac.pending_state = None
                return
            
            # Infer new state
            new_state = self._infer_state(ac)
            
            # Apply hysteresis
            self._apply_hysteresis(ac, new_state)
    
    def _check_teleport(self, ac: AircraftTrackingData) -> bool:
        """Check if aircraft teleported (moved > 5nm in one frame)."""
        if ac.prev_latitude == 0 and ac.prev_longitude == 0:
            return False  # First update, no previous position
        
        distance = self._haversine_nm(
            ac.prev_latitude, ac.prev_longitude,
            ac.latitude, ac.longitude
        )
        return distance > self.TELEPORT_THRESHOLD_NM
    
    def _infer_state(self, ac: AircraftTrackingData) -> TrafficState:
        """Infer aircraft state from telemetry."""
        spd = ac.airspeed
        alt = ac.altitude
        vs = ac.vertical_speed
        on_ground = ac.on_ground
        
        # Ground states
        if on_ground:
            if spd < 1:
                return TrafficState.PARKED
            elif spd < 5:
                # Check if moving backwards (pushback)
                # Simplified: assume low speed forward = taxi, could add heading change detection
                return TrafficState.PUSHBACK if self._is_reversing(ac) else TrafficState.TAXIING
            elif spd < 40:
                return TrafficState.TAXIING
            else:
                return TrafficState.TAKEOFF_ROLL
        
        # Air states
        else:
            # Just took off
            if ac.prev_on_ground:
                return TrafficState.AIRBORNE
            
            # Approach detection: low altitude, descending
            if alt < 3000 and vs < -200:
                return TrafficState.APPROACH
            
            # General airborne
            return TrafficState.AIRBORNE
    
    def _is_reversing(self, ac: AircraftTrackingData) -> bool:
        """Check if aircraft is moving backwards (pushback)."""
        # Simplified heuristic: check if heading is opposite to movement direction
        # Full implementation would check ground track vs heading
        return False  # Placeholder
    
    def _apply_hysteresis(self, ac: AircraftTrackingData, new_state: TrafficState):
        """Apply hysteresis to prevent state flicker."""
        now = time.time()
        
        if new_state == ac.state:
            # State unchanged, clear pending
            ac.pending_state = None
            return
        
        if new_state == ac.pending_state:
            # Same pending state, check if hysteresis period elapsed
            if now - ac.pending_state_start >= self.HYSTERESIS_SECONDS:
                # Confirm state change
                old_state = ac.state
                ac.state = new_state
                ac.pending_state = None
                
                # Emit event
                self._emit_state_change(ac, old_state, new_state)
        else:
            # New pending state
            ac.pending_state = new_state
            ac.pending_state_start = now
    
    def _emit_state_change(self, ac: AircraftTrackingData, old_state: TrafficState, new_state: TrafficState):
        """Emit traffic event on confirmed state change."""
        event_data = {
            'callsign': ac.callsign,
            'old_state': old_state.name,
            'new_state': new_state.name,
            'latitude': ac.latitude,
            'longitude': ac.longitude,
            'altitude': ac.altitude,
            'heading': ac.heading,
            'airspeed': ac.airspeed,
            'voice_id': ac.voice_id
        }
        
        print(f"TrafficStateManager: {ac.callsign} state: {old_state.name} -> {new_state.name}")
        event_bus.emit('traffic_state_change', event_data)
    
    def _cleanup_stale(self):
        """Remove aircraft not seen for a while."""
        now = time.time()
        stale = []
        
        with self.lock:
            for callsign, ac in self.aircraft.items():
                if now - ac.last_seen > self.STALE_TIMEOUT:
                    stale.append(callsign)
            
            for callsign in stale:
                del self.aircraft[callsign]
                print(f"TrafficStateManager: Removed stale aircraft: {callsign}")
    
    def _assign_voice(self, callsign: str) -> str:
        """Assign a consistent TTS voice based on callsign hash."""
        voices = [
            "en-GB-RyanNeural",
            "en-US-GuyNeural",
            "en-AU-WilliamNeural",
            "en-IN-PrabhatNeural",
            "en-GB-ThomasNeural",
            "en-US-ChristopherNeural",
            "en-AU-NatashaNeural",
            "en-GB-SoniaNeural",
        ]
        hash_val = int(hashlib.md5(callsign.encode()).hexdigest(), 16)
        return voices[hash_val % len(voices)]
    
    def _haversine_nm(self, lat1, lon1, lat2, lon2) -> float:
        """Calculate distance between two points in nautical miles."""
        R = 3440.065  # Earth radius in nm
        
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))
        
        return R * c
    
    def get_traffic_in_context(self, frequency_type: str) -> list:
        """
        Get list of aircraft relevant to the current frequency context.
        
        frequency_type: 'ground', 'tower', 'approach', 'center'
        """
        result = []
        
        with self.lock:
            for callsign, ac in self.aircraft.items():
                if self._matches_frequency_context(ac, frequency_type):
                    result.append({
                        'callsign': callsign,
                        'state': ac.state.name,
                        'altitude': ac.altitude,
                        'airspeed': ac.airspeed
                    })
        
        return result
    
    def _matches_frequency_context(self, ac: AircraftTrackingData, freq_type: str) -> bool:
        """Check if aircraft matches the frequency context filter."""
        if freq_type == 'ground':
            # Ground: on_ground, speed < 40kts
            return ac.on_ground and ac.airspeed < 40
        
        elif freq_type == 'tower':
            # Tower: on_ground fast (takeoff/landing roll) OR low altitude
            return (ac.on_ground and ac.airspeed >= 40) or (not ac.on_ground and ac.altitude < 3000)
        
        elif freq_type == 'approach':
            # Approach: airborne, below 10000ft, descending
            return not ac.on_ground and ac.altitude < 10000 and ac.vertical_speed < 0
        
        elif freq_type == 'center':
            # Center: high altitude
            return not ac.on_ground and ac.altitude >= 10000
        
        return True  # Default: show all
