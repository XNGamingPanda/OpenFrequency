"""
XPlaneProvider - X-Plane adapter using the official Local Web API.
"""
import requests

from .sim_provider import SimProvider


class XPlaneProvider(SimProvider):
    """X-Plane data provider using the official Local Web API."""

    # Track last error to avoid spamming logs
    _last_traffic_error = None
    _traffic_error_count = 0
    _max_traffic_error_log = 3  # Only log first 3 errors

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
        'aircraft_icao': 'sim/aircraft/view/acf_ICAO',
        'aircraft_description': 'sim/aircraft/view/acf_descrip',
    }

    # TCAS target arrays — LiveTraffic and other AI traffic plugins inject here
    TCAS_DREFS = {
        'lat':       'sim/cockpit2/tcas/targets/position/lat',
        'lon':       'sim/cockpit2/tcas/targets/position/lon',
        'ele':       'sim/cockpit2/tcas/targets/position/ele',       # elevation in metres MSL
        'vvi':       'sim/cockpit2/tcas/targets/position/vvi',       # vertical speed m/s
        'psi':       'sim/cockpit2/tcas/targets/position/psi',       # true heading degrees
        'speed':     'sim/cockpit2/tcas/targets/position/groundspeed', # m/s
        'on_ground': 'sim/cockpit2/tcas/targets/position/weight_on_wheels',
        'flight_id': 'sim/cockpit2/tcas/targets/flight_id',
    }

    FAILURE_DREFS = {
        'TOGGLE_ENGINE1_FAILURE': [
            ('sim/operation/failures/rel_engfai0', 6),
        ],
        'TOGGLE_ENGINE2_FAILURE': [
            ('sim/operation/failures/rel_engfai1', 6),
        ],
        'TOGGLE_HYDRAULIC_FAILURE': [
            ('sim/operation/failures/rel_hydpmp', 6),
        ],
        'TOGGLE_ELECTRICAL_FAILURE': [
            ('sim/operation/failures/rel_gen_esys', 6),
        ],
        'TOGGLE_GEAR_STUCK': [
            ('sim/operation/failures/rel_gear_act', 6),
            ('sim/operation/failures/rel_lagear1', 6),
            ('sim/operation/failures/rel_lagear2', 6),
            ('sim/operation/failures/rel_lagear3', 6),
        ],
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
            return False
        dref_name = self.DATAREFS.get(key, key)
        try:
            dataref_id = self._get_dataref_id(dref_name)
            self._request('PATCH', f'/datarefs/{dataref_id}/value', json={'data': value})
            return True
        except Exception as e:
            print(f"XPlaneProvider: Failed to set {dref_name} - {e}")
            return False

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
        # Auto-detect whether X-Plane uses Hz or kHz by reading current value.
        # X-Plane 12 Web API may return Hz (e.g. 118025000) while older versions use kHz (118025).
        raw = self._get_dref('com1', 0)
        try:
            raw_val = float(raw or 0)
        except Exception:
            raw_val = 0.0
        if raw_val > 1_000_000:
            # X-Plane is using Hz format
            freq_int = int(round(float(frequency) * 1_000_000))
        else:
            # X-Plane is using kHz format
            freq_int = int(round(float(frequency) * 1000))
        return self._set_dref('com1', freq_int)

    def get_com1_frequency(self) -> float:
        raw = self._get_dref('com1', 0)
        try:
            value = float(raw or 0)
        except Exception:
            return 0.0
        if value > 1_000_000:
            value /= 1_000_000.0
        elif value > 10_000:
            value /= 1000.0
        return round(value, 3)

    def get_aircraft_identity(self) -> dict:
        icao = str(self._get_dref('aircraft_icao', '') or '').strip().upper()
        description = str(self._get_dref('aircraft_description', '') or '').strip()
        return {
            'aircraft_icao': icao,
            'aircraft_title': description or icao,
            'aircraft_type': icao or description,
        }

    # ── Autopilot write-back (radar vectoring) ────────────────────────────────

    # Datarefs used to command the X-Plane default autopilot.
    # Note: aircraft with custom avionics (FBW, ZIBO) may ignore these.
    _AP_DREFS = {
        'heading_bug':    'sim/autopilot/heading_mag',       # degrees magnetic
        'altitude_target':'sim/autopilot/altitude',           # feet MSL
        'speed_target':   'sim/autopilot/airspeed',           # knots IAS
        'ap_state':       'sim/cockpit/autopilot/autopilot_state',
        'hnav_armed':     'sim/cockpit2/autopilot/heading_mode',
        'alt_armed':      'sim/cockpit2/autopilot/altitude_mode',
        'speed_armed':    'sim/cockpit2/autopilot/speed_mode',
    }

    def set_autopilot_heading(self, heading_deg: float) -> bool:
        """Set heading bug and engage heading hold."""
        try:
            hdg_id  = self._get_dataref_id(self._AP_DREFS['heading_bug'])
            self._request('PATCH', f'/datarefs/{hdg_id}/value',
                          json={'data': float(heading_deg % 360)})
            # Engage heading hold via command
            self._xp_command('sim/autopilot/heading')
            return True
        except Exception as e:
            print(f"XPlaneProvider: set_autopilot_heading failed - {e}")
            return False

    def set_autopilot_altitude(self, altitude_ft: float) -> bool:
        """Set altitude target and engage altitude hold."""
        try:
            alt_id = self._get_dataref_id(self._AP_DREFS['altitude_target'])
            self._request('PATCH', f'/datarefs/{alt_id}/value',
                          json={'data': float(altitude_ft)})
            self._xp_command('sim/autopilot/altitude_hold')
            return True
        except Exception as e:
            print(f"XPlaneProvider: set_autopilot_altitude failed - {e}")
            return False

    def set_autopilot_speed(self, speed_kt: float) -> bool:
        """Set airspeed target and engage autothrottle / speed hold."""
        try:
            spd_id = self._get_dataref_id(self._AP_DREFS['speed_target'])
            self._request('PATCH', f'/datarefs/{spd_id}/value',
                          json={'data': float(speed_kt)})
            self._xp_command('sim/autopilot/autothrottle_toggle')
            return True
        except Exception as e:
            print(f"XPlaneProvider: set_autopilot_speed failed - {e}")
            return False

    def _xp_command(self, command_path: str):
        """Send an X-Plane command (begin+end = single press)."""
        try:
            # Look up the command dataref ID via the commands endpoint
            resp = self.session.get(
                f"{self.base_url}/commands",
                params={'filter[name]': command_path},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json().get('data', [])
            if data:
                cmd_id = data[0]['id']
                self.session.post(
                    f"{self.base_url}/commands/{cmd_id}/activate",
                    timeout=self.timeout,
                )
        except Exception:
            pass

    def trigger_event(self, event_name: str):
        if event_name not in self.FAILURE_DREFS:
            return False
        ok = False
        for dref_name, value in self.FAILURE_DREFS[event_name]:
            ok = self._set_dref(dref_name, value) or ok
        return ok

    def _parse_flight_id(self, raw_id, index) -> str:
        """Parse a flight_id entry from TCAS array (handles byte-array, string, or flat-char formats)."""
        if index >= len(raw_id):
            return ''
        fid = raw_id[index]
        if isinstance(fid, list):
            # List of chars or ints (byte array)
            chars = []
            for c in fid:
                if isinstance(c, int):
                    if c == 0:
                        break
                    chars.append(chr(c))
                elif isinstance(c, str):
                    if c == '\x00' or c == '':
                        break
                    chars.append(c)
            return ''.join(chars).strip()
        return str(fid).strip().rstrip('\x00')

    def get_traffic_targets(self) -> list:
        """Read TCAS target arrays (populated by LiveTraffic and other AI traffic plugins)."""
        if not self._connected:
            return []
        try:
            results = {}
            for key, dref_name in self.TCAS_DREFS.items():
                try:
                    dataref_id = self._get_dataref_id(dref_name)
                    result = self._request('GET', f'/datarefs/{dataref_id}/value')
                    raw = result.get('data', result)
                    if isinstance(raw, list):
                        results[key] = raw
                    elif isinstance(raw, dict) and 'value' in raw:
                        v = raw['value']
                        results[key] = v if isinstance(v, list) else [v]
                    else:
                        results[key] = []
                except Exception as e:
                    error_str = str(e)
                    if self._traffic_error_count < self._max_traffic_error_log:
                        print(f"XPlaneProvider: TCAS dref '{key}' failed - {e}")
                        self._traffic_error_count += 1
                        self._last_traffic_error = error_str
                    results[key] = []

            lat_arr = results.get('lat', [])
            if not lat_arr:
                return []

            targets = []
            raw_flight_ids = results.get('flight_id', [])
            for i in range(len(lat_arr)):
                try:
                    lat = float(lat_arr[i] or 0)
                    lon_arr = results.get('lon', [])
                    lon = float(lon_arr[i] if i < len(lon_arr) else 0)
                    # Empty TCAS slot
                    if lat == 0.0 and lon == 0.0:
                        continue

                    def _f(key, idx):
                        arr = results.get(key, [])
                        return arr[idx] if idx < len(arr) else 0

                    ele_m = float(_f('ele', i) or 0)
                    alt_ft = ele_m * 3.28084
                    vvi_ms = float(_f('vvi', i) or 0)
                    vs_fpm = vvi_ms * 196.85
                    spd_ms = float(_f('speed', i) or 0)
                    spd_kt = spd_ms * 1.94384
                    hdg = float(_f('psi', i) or 0)
                    on_ground = bool(_f('on_ground', i))

                    callsign = self._parse_flight_id(raw_flight_ids, i) or f'TCAS{i+1:02d}'

                    targets.append({
                        'callsign': callsign,
                        'latitude': lat,
                        'longitude': lon,
                        'altitude': alt_ft,
                        'heading': hdg,
                        'airspeed': spd_kt,
                        'vertical_speed': vs_fpm,
                        'on_ground': on_ground,
                    })
                except Exception:
                    continue
            return targets
        except Exception as e:
            error_str = str(e)
            if self._traffic_error_count < self._max_traffic_error_log:
                print(f"XPlaneProvider: get_traffic_targets failed - {e}")
                self._last_traffic_error = error_str
                self._traffic_error_count += 1
            elif error_str != self._last_traffic_error:
                print(f"XPlaneProvider: get_traffic_targets failed - {e}")
                self._last_traffic_error = error_str
            return []
