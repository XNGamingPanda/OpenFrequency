"""
SimConnectProvider - MSFS/P3D/FSX adapter using SimConnect.
Implements SimProvider interface for Microsoft simulators.
"""
from .sim_provider import SimProvider


class SimConnectProvider(SimProvider):
    """SimConnect data provider for MSFS, P3D, and FSX."""
    
    def __init__(self, sim_type='msfs'):
        self.sim_type = sim_type
        self.sc = None
        self.aq = None
        self._connected = False
    
    @property
    def name(self) -> str:
        names = {
            'msfs': 'Microsoft Flight Simulator',
            'p3d': 'Prepar3D',
            'fsx': 'FSX'
        }
        return names.get(self.sim_type, 'SimConnect')
    
    def connect(self) -> bool:
        """Connect to the simulator via SimConnect."""
        try:
            from SimConnect import SimConnect, AircraftRequests
            self.sc = SimConnect()
            self.aq = AircraftRequests(self.sc, _time=200)
            self._connected = True
            print(f"SimConnectProvider: Connected to {self.name}")
            return True
        except ImportError:
            print("SimConnectProvider: SimConnect library not installed. Run: pip install SimConnect")
            return False
        except Exception as e:
            print(f"SimConnectProvider: Failed to connect - {e}")
            self._connected = False
            return False
    
    def disconnect(self):
        """Disconnect from SimConnect."""
        if self.sc:
            try:
                self.sc.exit()
            except:
                pass
            self.sc = None
            self.aq = None
        self._connected = False
        print("SimConnectProvider: Disconnected")
    
    def is_connected(self) -> bool:
        return self._connected
    
    def _get(self, key, default=0):
        """Safely get a SimConnect variable."""
        if not self.aq:
            return default
        try:
            value = self.aq.get(key)
            return value if value is not None else default
        except Exception:
            return default
    
    # ===== READ OPERATIONS =====
    
    def get_position(self) -> dict:
        return {
            'latitude': self._get('PLANE_LATITUDE', 0),
            'longitude': self._get('PLANE_LONGITUDE', 0),
            'altitude': self._get('PLANE_ALTITUDE', 0)
        }
    
    def get_attitude(self) -> dict:
        return {
            'heading': self._get('PLANE_HEADING_DEGREES_TRUE', 0),
            'pitch': self._get('PLANE_PITCH_DEGREES', 0),
            'bank': self._get('PLANE_BANK_DEGREES', 0)
        }
    
    def get_airspeed(self) -> float:
        return self._get('AIRSPEED_INDICATED', 0)
    
    def get_vertical_speed(self) -> float:
        return self._get('VERTICAL_SPEED', 0)
    
    def get_engine_data(self) -> dict:
        return {
            'n1': self._get('ENG_N1_RPM:1', 0),
            'egt': self._get('ENG_EXHAUST_GAS_TEMPERATURE:1', 0),
            'fuel_flow': self._get('ENG_FUEL_FLOW_GPH:1', 0)
        }
    
    def get_gear_status(self) -> bool:
        return self._get('GEAR_TOTAL_PCT_EXTENDED', 0) > 0.5
    
    def get_flaps_position(self) -> float:
        return self._get('TRAILING_EDGE_FLAPS_LEFT_PERCENT', 0)
    
    # ===== WRITE OPERATIONS =====
    
    def set_transponder(self, code: int):
        if self.sc:
            try:
                from SimConnect import AircraftEvents
                ae = AircraftEvents(self.sc)
                ae.find('XPNDR_SET').trigger(code)
            except Exception as e:
                print(f"SimConnectProvider: Failed to set transponder - {e}")
    
    def set_com1_frequency(self, frequency: float):
        if self.sc:
            try:
                from SimConnect import AircraftEvents
                ae = AircraftEvents(self.sc)
                # Convert to BCD format for SimConnect
                freq_khz = int(frequency * 1000)
                ae.find('COM_RADIO_SET_HZ').trigger(freq_khz * 1000)
            except Exception as e:
                print(f"SimConnectProvider: Failed to set COM1 - {e}")
    
    # ===== EVENTS =====
    
    def trigger_event(self, event_name: str):
        """Trigger a SimConnect event."""
        if self.sc:
            try:
                from SimConnect import AircraftEvents
                ae = AircraftEvents(self.sc)
                event = ae.find(event_name)
                if event:
                    event.trigger()
                    print(f"SimConnectProvider: Triggered event {event_name}")
            except Exception as e:
                print(f"SimConnectProvider: Failed to trigger {event_name} - {e}")
