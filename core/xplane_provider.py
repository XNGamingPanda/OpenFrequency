"""
XPlaneProvider - X-Plane adapter using the official Local Web API.
"""
import requests

from .sim_provider import SimProvider


class XPlaneProvider(SimProvider):
    """X-Plane data provider using the official Local Web API."""

    DATAREFS = {
        'latitude': 'sim/flightmodel/position/latitude',
        'longitude': 'sim/flightmodel/position/longitude',
        'altitude_m': 'sim/flightmodel/position/elevation',
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

    FAILURE_DREFS = {
        'TOGGLE_ENGINE1_FAILURE': ('sim/operation/failures/rel_engfai0', 6),
        'TOGGLE_ENGINE2_FAILURE': ('sim/operation/failures/rel_engfai1', 6),
        'TOGGLE_HYDRAULIC_FAILURE': ('sim/operation/failures/rel_hydpmp', 6),
        'TOGGLE_ELECTRICAL_FAILURE': ('sim/operation/failures/rel_elec_sys', 6),
    }

    def __init__(self, host='127.0.0.1', port=8086):
        self.host = host
        self.port = int(port)
        self.base_url = f"http://{self.host}:{self.port}/api/v3"
        self.capabilities_url = f"http://{self.host}:{self.port}/api/capabilities"
        self.session = requests.Session()
        self.session.headers.update({'Accept': 'application/json'})
        self.timeout = 1.5
        self._connected = False
        self._dataref_ids = {}

    @property
    def name(self) -> str:
        return "X-Plane"

    def _request(self, method, path, **kwargs):
        response = self.session.request(
            method,
            f"{self.base_url}{path}",
            timeout=kwargs.pop('timeout', self.timeout),
            **kwargs,
        )
        response.raise_for_status()
        if response.content:
            return response.json()
        return {}

    def connect(self) -> bool:
        """Connect to X-Plane via the official Local Web API."""
        try:
            capabilities = self.session.get(self.capabilities_url, timeout=self.timeout)
            capabilities.raise_for_status()
            self._connected = True
            print(f"XPlaneProvider: Connected to X-Plane Web API at {self.host}:{self.port}")
            return True
        except requests.Timeout:
            print(
                f"XPlaneProvider: Timed out connecting to Local Web API at {self.host}:{self.port}. "
                "Enable the X-Plane Local Web API and verify the configured port."
            )
        except requests.RequestException as e:
            print(f"XPlaneProvider: Failed to connect to Local Web API - {e}")
        self._connected = False
        return False

    def disconnect(self):
        self.session.close()
        self._connected = False
        print("XPlaneProvider: Disconnected")

    def is_connected(self) -> bool:
        return self._connected

    def _extract_value(self, payload, default=0):
        if isinstance(payload, dict):
            if 'data' in payload:
                return self._extract_value(payload['data'], default)
            if 'value' in payload:
                return self._extract_value(payload['value'], default)
        if isinstance(payload, list):
            if not payload:
                return default
            if isinstance(payload[0], dict) and 'value' in payload[0]:
                return self._extract_value(payload[0]['value'], default)
            return payload[0]
        return payload if payload is not None else default

    def _get_dataref_id(self, dref_name):
        cached = self._dataref_ids.get(dref_name)
        if cached is not None:
            return cached

        result = self._request(
            'GET',
            '/datarefs',
            params={'filter[name]': dref_name},
        )
        data = result.get('data', result)
        if isinstance(data, list):
            for item in data:
                if item.get('name') == dref_name:
                    dataref_id = item.get('id')
                    if dataref_id is not None:
                        self._dataref_ids[dref_name] = dataref_id
                        return dataref_id
            if data:
                dataref_id = data[0].get('id')
                if dataref_id is not None:
                    self._dataref_ids[dref_name] = dataref_id
                    return dataref_id
        raise KeyError(f"DataRef not found: {dref_name}")

    def _get_dref(self, key, default=0):
        if not self._connected:
            return default
        dref_name = self.DATAREFS.get(key, key)
        try:
            dataref_id = self._get_dataref_id(dref_name)
            result = self._request('GET', f'/datarefs/{dataref_id}/value')
            return self._extract_value(result, default)
        except Exception:
            return default

    def _set_dref(self, key, value):
        if not self._connected:
            return
        dref_name = self.DATAREFS.get(key, key)
        try:
            dataref_id = self._get_dataref_id(dref_name)
            self._request('PATCH', f'/datarefs/{dataref_id}/value', json={'data': value})
        except Exception as e:
            print(f"XPlaneProvider: Failed to set {dref_name} - {e}")

    def get_position(self) -> dict:
        alt_m = self._get_dref('altitude_m', 0)
        return {
            'latitude': self._get_dref('latitude', 0),
            'longitude': self._get_dref('longitude', 0),
            'altitude': float(alt_m) * 3.28084,
        }

    def get_attitude(self) -> dict:
        return {
            'heading': self._get_dref('heading', 0),
            'pitch': self._get_dref('pitch', 0),
            'bank': self._get_dref('bank', 0),
        }

    def get_airspeed(self) -> float:
        # X-Plane can report tiny negative IAS values while parked due to
        # physics jitter and instrument simulation. Clamp them to zero.
        return max(0.0, float(self._get_dref('airspeed', 0) or 0))

    def get_vertical_speed(self) -> float:
        return self._get_dref('vs_fpm', 0)

    def get_engine_data(self) -> dict:
        return {
            'n1': self._get_dref('n1', 0),
            'egt': self._get_dref('egt', 0),
            'fuel_flow': self._get_dref('fuel_flow', 0),
        }

    def get_gear_status(self) -> bool:
        return float(self._get_dref('gear_deploy', 0)) > 0.5

    def get_flaps_position(self) -> float:
        return float(self._get_dref('flaps', 0)) * 100

    def set_transponder(self, code: int):
        self._set_dref('transponder', code)

    def set_com1_frequency(self, frequency: float):
        freq_hz = int(float(frequency) * 1000)
        self._set_dref('com1', freq_hz)

    def trigger_event(self, event_name: str):
        if event_name not in self.FAILURE_DREFS:
            return
        dref_name, value = self.FAILURE_DREFS[event_name]
        self._set_dref(dref_name, value)
