import configparser
import os
import re
from pathlib import Path


class AircraftCatalog:
    """Discover locally installed aircraft and map them to career aircraft codes."""

    CANONICAL = {
        'C172': ['C172', 'CESSNA 172', 'C172SP'],
        'PA28': ['PA28', 'PIPER PA-28'],
        'C208': ['C208', 'CARAVAN'],
        'BE58': ['BE58', 'BARON 58'],
        'TBM9': ['TBM9', 'TBM 900', 'TBM 930', 'TBM 960'],
        'CRJ7': ['CRJ7', 'CRJ 700', 'CRJ-700'],
        'E175': ['E175', 'E75L', 'EMBRAER 175'],
        'B738': ['B738', '737-800', 'B737-800', 'ZIBO'],
        'A320': ['A320', 'A20N', 'AIRBUS A320'],
        'B77W': ['B77W', '777-300ER', 'B777-300ER'],
        'A350': ['A359', 'A350', 'AIRBUS A350'],
        'B748': ['B748', '747-8', 'B747-8'],
        'A380': ['A388', 'A380', 'AIRBUS A380'],
    }

    def __init__(self, config):
        self.config = config or {}

    def available_aircraft(self):
        provider = ((self.config.get('simulator') or {}).get('provider') or 'auto').lower()
        detected = []
        if provider == 'xplane':
            detected = self._scan_xplane()
        elif provider in {'msfs', 'p3d', 'fsx', 'auto'}:
            detected = self._scan_msfs()
        return sorted(set(detected))

    def current_aircraft(self):
        # Filled by SimBridge when simulator APIs expose the current aircraft.
        return None

    def canonical_from_text(self, text):
        return self._canonical_from_text(text)

    def allowed_for_rank(self, rank_index, fallback):
        installed = self.available_aircraft()
        if not installed:
            return list(fallback)
        allowed = set()
        for idx, aircraft in fallback.items() if isinstance(fallback, dict) else []:
            if idx <= rank_index:
                allowed.update(aircraft)
        if not allowed:
            allowed.update(fallback if isinstance(fallback, list) else [])
        selected = [aircraft for aircraft in installed if aircraft in allowed]
        return selected or list(fallback.get(rank_index, ['C172']) if isinstance(fallback, dict) else fallback)

    def _scan_xplane(self):
        root = ((self.config.get('simulator') or {}).get('xplane_root') or '').strip()
        if not root or not os.path.isdir(root):
            return []
        aircraft_dir = Path(root) / 'Aircraft'
        if not aircraft_dir.exists():
            return []
        found = []
        for acf in aircraft_dir.rglob('*.acf'):
            if any(part.startswith('.') for part in acf.parts):
                continue
            text = self._read_small_text(acf, limit=300_000)
            haystack = f"{acf.stem} {acf.parent.name} {text}".upper()
            code = self._canonical_from_text(haystack)
            if code:
                found.append(code)
        return found

    def _scan_msfs(self):
        roots = self._msfs_roots()
        found = []
        for root in roots:
            if not root.exists():
                continue
            for cfg in root.rglob('aircraft.cfg'):
                code = self._canonical_from_msfs_cfg(cfg)
                if code:
                    found.append(code)
        return found

    def _msfs_roots(self):
        sim_config = self.config.get('simulator') or {}
        configured = sim_config.get('msfs_packages_root')
        roots = []
        if configured:
            roots.append(Path(configured))
        local = os.environ.get('LOCALAPPDATA')
        appdata = os.environ.get('APPDATA')
        if local:
            roots += [
                Path(local) / 'Packages' / 'Microsoft.FlightSimulator_8wekyb3d8bbwe' / 'LocalCache' / 'Packages' / 'Official',
                Path(local) / 'Packages' / 'Microsoft.FlightSimulator_8wekyb3d8bbwe' / 'LocalCache' / 'Packages' / 'Community',
            ]
        if appdata:
            roots += [
                Path(appdata) / 'Microsoft Flight Simulator' / 'Packages' / 'Official',
                Path(appdata) / 'Microsoft Flight Simulator' / 'Packages' / 'Community',
            ]
        return roots

    def _canonical_from_msfs_cfg(self, cfg_path):
        raw = self._read_small_text(cfg_path, limit=500_000)
        haystack = f"{cfg_path.parent.name} {raw}".upper()
        icao_match = re.search(r'icao_type_designator\s*=\s*"?([^"\r\n]+)"?', raw, re.IGNORECASE)
        if icao_match:
            code = icao_match.group(1).strip().upper()
            if code in self.CANONICAL:
                return code
        return self._canonical_from_text(haystack)

    def _canonical_from_text(self, text):
        text = (text or '').upper()
        for code, tokens in self.CANONICAL.items():
            if any(token.upper() in text for token in tokens):
                return code
        return None

    def _read_small_text(self, path, limit=200_000):
        try:
            with open(path, 'rb') as f:
                data = f.read(limit)
            return data.decode('utf-8', errors='ignore')
        except Exception:
            return ''
