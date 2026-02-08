"""
XPlaneProvider - X-Plane adapter using XPlaneConnect (UDP).
Implements SimProvider interface for cross-platform support.
"""
from .sim_provider import SimProvider


class XPlaneProvider(SimProvider):
    """X-Plane data provider using NASA XPlaneConnect library."""
    
    # X-Plane DataRef paths
    DATAREFS = {
        'latitude': 'sim/flightmodel/position/latitude',
        'longitude': 'sim/flightmodel/position/longitude',
        'altitude_m': 'sim/flightmodel/position/elevation',  # meters
        'heading': 'sim/flightmodel/position/psi',
        'pitch': 'sim/flightmodel/position/theta',
        'bank': 'sim/flightmodel/position/phi',
        'airspeed': 'sim/flightmodel/position/indicated_airspeed',
        'vs_fpm': 'sim/flightmodel/position/vh_ind_fpm',
        'n1': 'sim/flightmodel/engine/ENGN_N1_[0]',
        'egt': 'sim/flightmodel/engine/ENGN_EGT_c[0]',
        'fuel_flow': 'sim/flightmodel/engine/ENGN_FF_[0]',
        'gear_deploy': 'sim/aircraft/parts/acf_gear_deploy',
        'flaps': 'sim/flightmodel/controls/flaprat',
        'transponder': 'sim/cockpit/radios/transponder_code',
        'com1': 'sim/cockpit2/radios/actuators/com1_frequency_hz_833',
    }
    
    def __init__(self, host='127.0.0.1', port=49009):
        self.host = host
        self.port = port
        self.client = None
        self._connected = False
    
    @property
    def name(self) -> str:
        return "X-Plane"
    
    def connect(self) -> bool:
        """Connect to X-Plane via XPlaneConnect UDP."""
        try:
            import xpc
            self.client = xpc.XPlaneConnect(self.host, xpPort=self.port)
            # Test connection by reading a value
            self.client.getDREF('sim/test/test_float')
            self._connected = True
            print(f"XPlaneProvider: Connected to X-Plane at {self.host}:{self.port}")
            return True
        except ImportError:
            print("XPlaneProvider: xpc library not installed. Run: pip install XPlaneConnect")
            return False
        except Exception as e:
            print(f"XPlaneProvider: Failed to connect - {e}")
            self._connected = False
            return False
    
    def disconnect(self):
        """Disconnect from X-Plane."""
        if self.client:
            self.client.close()
            self.client = None
        self._connected = False
        print("XPlaneProvider: Disconnected")
    
    def is_connected(self) -> bool:
        return self._connected
    
    def _get_dref(self, key, default=0):
        """Safely get a DataRef value."""
        if not self.client:
            return default
        try:
            dref = self.DATAREFS.get(key)
            if dref:
                values = self.client.getDREF(dref)
                return values[0] if values else default
        except Exception:
            pass
        return default
    
    def _set_dref(self, key, value):
        """Safely set a DataRef value."""
        if not self.client:
            return
        try:
            dref = self.DATAREFS.get(key)
            if dref:
                self.client.sendDREF(dref, value)
        except Exception as e:
            print(f"XPlaneProvider: Failed to set {key} - {e}")
    
    # ===== READ OPERATIONS =====
    
    def get_position(self) -> dict:
        alt_m = self._get_dref('altitude_m', 0)
        return {
            'latitude': self._get_dref('latitude', 0),
            'longitude': self._get_dref('longitude', 0),
            'altitude': alt_m * 3.28084  # meters to feet
        }
    
    def get_attitude(self) -> dict:
        return {
            'heading': self._get_dref('heading', 0),
            'pitch': self._get_dref('pitch', 0),
            'bank': self._get_dref('bank', 0)
        }
    
    def get_airspeed(self) -> float:
        return self._get_dref('airspeed', 0)
    
    def get_vertical_speed(self) -> float:
        return self._get_dref('vs_fpm', 0)
    
    def get_engine_data(self) -> dict:
        return {
            'n1': self._get_dref('n1', 0),
            'egt': self._get_dref('egt', 0),
            'fuel_flow': self._get_dref('fuel_flow', 0)
        }
    
    def get_gear_status(self) -> bool:
        return self._get_dref('gear_deploy', 0) > 0.5
    
    def get_flaps_position(self) -> float:
        return self._get_dref('flaps', 0) * 100  # ratio to percentage
    
    # ===== WRITE OPERATIONS =====
    
    def set_transponder(self, code: int):
        self._set_dref('transponder', code)
    
    def set_com1_frequency(self, frequency: float):
        # X-Plane uses Hz for 8.33kHz spacing
        freq_hz = int(frequency * 1000)
        self._set_dref('com1', freq_hz)
    
    # ===== EVENTS =====
    
    def trigger_event(self, event_name: str):
        """X-Plane uses DataRefs for failures, not events like MSFS."""
        failure_map = {
            'TOGGLE_ENGINE1_FAILURE': ('sim/operation/failures/rel_engfai0', 6),
            'TOGGLE_HYDRAULIC_FAILURE': ('sim/operation/failures/rel_hydpmp', 6),
            'TOGGLE_ELECTRICAL_FAILURE': ('sim/operation/failures/rel_elec_sys', 6),
        }
        if event_name in failure_map and self.client:
            dref, value = failure_map[event_name]
            try:
                self.client.sendDREF(dref, value)
            except Exception as e:
                print(f"XPlaneProvider: Failed to trigger {event_name} - {e}")
