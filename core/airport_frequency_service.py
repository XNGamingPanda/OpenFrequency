import csv
import math
import os
import threading
from collections import defaultdict

import requests

from .simulator_ground_service import SimulatorGroundService


class AirportFrequencyService:
    DATA_URL = "https://raw.githubusercontent.com/davidmegginson/ourairports-data/refs/heads/main/airport-frequencies.csv"
    AIRPORTS_URL = "https://raw.githubusercontent.com/davidmegginson/ourairports-data/refs/heads/main/airports.csv"
    RUNWAYS_URL = "https://raw.githubusercontent.com/davidmegginson/ourairports-data/refs/heads/main/runways.csv"
    DEFAULT_CENTER_FREQUENCY = 132.450

    def __init__(self, config):
        self.config = config
        self.cache_dir = os.path.join("data", "airport_data")
        self.cache_path = os.path.join(self.cache_dir, "airport-frequencies.csv")
        self.airports_cache_path = os.path.join(self.cache_dir, "airports.csv")
        self.runways_cache_path = os.path.join(self.cache_dir, "runways.csv")
        self._lock = threading.Lock()
        self._freq_by_airport = defaultdict(list)
        self._airport_positions = {}
        self._runways_by_airport = defaultdict(list)
        self._loaded = False
        self.simulator_ground_service = SimulatorGroundService(config)

    def update_config(self, config):
        self.config = config
        self.simulator_ground_service.update_config(config)

    def load(self):
        return self.load_cached_or_download(force_update=False)

    def load_cached_or_download(self, force_update=False):
        os.makedirs(self.cache_dir, exist_ok=True)
        csv_text = self._load_dataset(self.DATA_URL, self.cache_path, "frequency data", force_update=force_update)
        airports_csv_text = self._load_dataset(self.AIRPORTS_URL, self.airports_cache_path, "airport position data", force_update=force_update)
        runways_csv_text = self._load_dataset(self.RUNWAYS_URL, self.runways_cache_path, "runway data", force_update=force_update)

        if not csv_text or not airports_csv_text or not runways_csv_text:
            print("AirportFrequencyService: Required airport data is unavailable.")
            return False

        self._parse_airports_csv(airports_csv_text)
        self._parse_runways_csv(runways_csv_text)
        self._parse_csv(csv_text)
        return True

    def _load_dataset(self, url, cache_path, label, force_update=False):
        if not force_update and os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    print(f"AirportFrequencyService: Loaded cached {label} from {cache_path}")
                    return f.read()
            except Exception as e:
                print(f"AirportFrequencyService: Failed reading cached {label}, trying download - {e}")

        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            csv_text = resp.text
            with open(cache_path, "w", encoding="utf-8", newline="") as f:
                f.write(csv_text)
            print(f"AirportFrequencyService: Downloaded latest {label} to {cache_path}")
            return csv_text
        except Exception as e:
            print(f"AirportFrequencyService: {label} download failed, falling back to cache - {e}")
            if os.path.exists(cache_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    return f.read()
        return None

    def _parse_airports_csv(self, csv_text):
        airport_positions = {}
        reader = csv.DictReader(csv_text.splitlines())
        for row in reader:
            ident = (row.get("ident") or "").strip().upper()
            name = (row.get("name") or "").strip()
            lat_raw = (row.get("latitude_deg") or "").strip()
            lon_raw = (row.get("longitude_deg") or "").strip()
            if not ident or not lat_raw or not lon_raw:
                continue
            try:
                airport_positions[ident] = {
                    "ident": ident,
                    "name": name,
                    "lat": float(lat_raw),
                    "lon": float(lon_raw),
                    "iso_country": (row.get("iso_country") or "").strip().upper(),
                    "type": (row.get("type") or "").strip().lower(),
                    "scheduled_service": (row.get("scheduled_service") or "").strip().lower(),
                }
            except ValueError:
                continue

        with self._lock:
            self._airport_positions = airport_positions
        print(f"AirportFrequencyService: Loaded airport positions for {len(airport_positions)} airports")

    def _parse_runways_csv(self, csv_text):
        runways_by_airport = defaultdict(list)
        reader = csv.DictReader(csv_text.splitlines())
        for row in reader:
            airport_ident = (row.get("airport_ident") or "").strip().upper()
            if not airport_ident or str(row.get("closed", "0")).strip() == "1":
                continue

            for end_key, heading_key in [('le_ident', 'le_heading_degT'), ('he_ident', 'he_heading_degT')]:
                ident = (row.get(end_key) or "").strip().upper()
                heading_raw = (row.get(heading_key) or "").strip()
                if not ident or not heading_raw:
                    continue
                try:
                    heading = float(heading_raw)
                except ValueError:
                    continue
                runways_by_airport[airport_ident].append({
                    "ident": ident,
                    "heading": heading,
                })

        with self._lock:
            self._runways_by_airport = runways_by_airport
        print(f"AirportFrequencyService: Loaded runways for {len(runways_by_airport)} airports")

    def _parse_csv(self, csv_text):
        freq_by_airport = defaultdict(list)
        reader = csv.DictReader(csv_text.splitlines())
        for row in reader:
            airport_ident = (row.get("airport_ident") or "").strip().upper()
            freq_raw = (row.get("frequency_mhz") or "").strip()
            if not airport_ident or not freq_raw:
                continue

            try:
                freq_mhz = round(float(freq_raw), 3)
            except ValueError:
                continue

            entry = {
                "id": row.get("id"),
                "airport_ident": airport_ident,
                "type": (row.get("type") or "").strip(),
                "description": (row.get("description") or "").strip(),
                "frequency_mhz": freq_mhz,
                "label": self._build_label(row, freq_mhz),
                "role": self._infer_role(row),
            }
            freq_by_airport[airport_ident].append(entry)

        for airport_ident, entries in freq_by_airport.items():
            entries.sort(key=lambda item: (item["frequency_mhz"], item["label"]))

        with self._lock:
            self._freq_by_airport = freq_by_airport
            self._loaded = True
        print(f"AirportFrequencyService: Loaded frequencies for {len(freq_by_airport)} airports")

    def _build_label(self, row, freq_mhz):
        description = (row.get("description") or "").strip()
        freq_str = f"{freq_mhz:.3f}"
        return f"{description} {freq_str}".strip()

    def _infer_role(self, row):
        type_text = (row.get('type') or '').strip().lower()
        desc_text = (row.get('description') or '').strip().lower()
        text = f"{type_text} {desc_text}".lower()

        exact_type_roles = {
            'atis': 'ATIS',
            'awos': 'ATIS',
            'asos': 'ATIS',
            'gnd': 'Ground',
            'twr': 'Tower',
            'app': 'Approach',
            'dep': 'Departure',
            'del': 'Clearance Delivery',
            'cld': 'Clearance Delivery',
            'ctr': 'Center',
            'cntr': 'Center',
            'center': 'Center',
            'radio': 'Unicom',
            'unicom': 'Unicom',
            'ctaf': 'Unicom',
        }
        if type_text in exact_type_roles:
            return exact_type_roles[type_text]

        if "atis" in text or "information" in text or "awos" in text or "asos" in text:
            return "ATIS"
        if "clearance" in text or "delivery" in text:
            return "Clearance Delivery"
        if "ground" in text or " gnd" in text:
            return "Ground"
        if "tower" in text or " twr" in text:
            return "Tower"
        if "departure" in text or " dep" in text:
            return "Departure"
        if "approach" in text or " app" in text:
            return "Approach"
        if "center" in text or "centre" in text or " ctr" in text:
            return "Center"
        if "unicom" in text or "ctaf" in text:
            return "Unicom"
        return "ATC"

    def get_airport_frequencies(self, airport_ident):
        airport_ident = (airport_ident or "").strip().upper()
        if not airport_ident:
            return []
        source = self.get_frequency_source()
        with self._lock:
            third_party_entries = [dict(item) for item in self._freq_by_airport.get(airport_ident, [])]
        if source == "simulator":
            simulator_entries = self.simulator_ground_service.get_airport_frequencies(airport_ident)
            if simulator_entries:
                merged = []
                seen = set()
                for entry in simulator_entries + third_party_entries:
                    key = (entry.get("role"), entry.get("frequency_mhz"))
                    if key in seen:
                        continue
                    seen.add(key)
                    merged.append(entry)
                return self._with_center_frequency(airport_ident, sorted(merged, key=lambda item: (item["frequency_mhz"], item["label"])))
        return self._with_center_frequency(airport_ident, third_party_entries)

    def _with_center_frequency(self, airport_ident, entries):
        entries = [dict(item) for item in entries]
        if not entries:
            return []
        if any((entry.get("role") or "").lower() == "center" for entry in entries):
            return entries

        center_frequency = self._configured_center_frequency()
        entries.append({
            "id": f"{airport_ident}-area-center",
            "airport_ident": airport_ident,
            "type": "CTR",
            "description": "Area Center",
            "frequency_mhz": center_frequency,
            "label": f"Area Center {center_frequency:.3f}",
            "role": "Center",
            "synthetic": True,
        })
        return sorted(entries, key=lambda item: (item["frequency_mhz"], item["label"]))

    def _configured_center_frequency(self):
        frequencies = self.config.get("frequencies", {}) or {}
        for key in ("Center", "center", "CTR", "ctr"):
            value = frequencies.get(key)
            if value:
                try:
                    return round(float(value), 3)
                except (TypeError, ValueError):
                    pass
        return self.DEFAULT_CENTER_FREQUENCY

    def get_frequency_source(self):
        navdata = self.config.get("navdata", {}) or {}
        return (navdata.get("frequency_source") or "third_party").lower()

    def get_frequency_map(self, airport_ident):
        result = {}
        for entry in self.get_airport_frequencies(airport_ident):
            role = entry.get("role")
            if role and role not in result:
                result[role] = f"{entry['frequency_mhz']:.3f}"
        return result

    def get_airport_name(self, airport_ident):
        airport_ident = (airport_ident or "").strip().upper()
        if not airport_ident:
            return ""
        with self._lock:
            airport = self._airport_positions.get(airport_ident, {})
        return (airport.get("name") or "").strip()

    def get_airport_position(self, airport_ident):
        airport_ident = (airport_ident or "").strip().upper()
        if not airport_ident:
            return None
        with self._lock:
            airport = self._airport_positions.get(airport_ident)
        return dict(airport) if airport else None

    def get_nearest_airport_ident(self, lat, lon):
        if lat is None or lon is None:
            return None
        with self._lock:
            airport_positions = dict(self._airport_positions)
        if not airport_positions:
            return None
        best_ident = None
        best_distance = None
        for ident, airport in airport_positions.items():
            distance = self._distance_nm(lat, lon, airport["lat"], airport["lon"])
            if best_distance is None or distance < best_distance:
                best_ident = ident
                best_distance = distance
        return best_ident

    def get_preferred_runways(self, airport_ident, wind_dir=None, limit=2):
        airport_ident = (airport_ident or "").strip().upper()
        with self._lock:
            runways = [dict(item) for item in self._runways_by_airport.get(airport_ident, [])]

        if not runways:
            return []

        def angle_diff(a, b):
            delta = abs(float(a) - float(b)) % 360
            return min(delta, 360 - delta)

        if wind_dir is None:
            reference_heading = runways[0]["heading"]
            unique = []
            seen = set()
            for runway in runways:
                if angle_diff(runway["heading"], reference_heading) > 45:
                    continue
                if runway['ident'] not in seen:
                    unique.append(runway['ident'])
                    seen.add(runway['ident'])
            return unique[:limit]

        sorted_runways = sorted(
            runways,
            key=lambda item: angle_diff(item['heading'], wind_dir)
        )

        selected = []
        seen = set()
        for runway in sorted_runways:
            if angle_diff(runway['heading'], wind_dir) > 90:
                continue
            ident = runway['ident']
            if ident not in seen:
                selected.append(ident)
                seen.add(ident)
            if len(selected) >= limit:
                break
        return selected

    def get_nearby_airports(self, lat, lon, sqlite_path, limit=6, radius_deg=1.5):
        if lat is None or lon is None:
            return []

        airports = self._get_nearby_airports_from_ourairports(lat, lon, limit=limit, radius_deg=radius_deg)
        if airports:
            return airports

        return self._get_nearby_airports_from_sqlite(lat, lon, sqlite_path, limit=limit, radius_deg=radius_deg)

    def _get_nearby_airports_from_ourairports(self, lat, lon, limit=6, radius_deg=1.5):
        with self._lock:
            airport_positions = dict(self._airport_positions)

        airports = []
        for ident, airport in airport_positions.items():
            apt_lat = airport["lat"]
            apt_lon = airport["lon"]
            if not (lat - radius_deg <= apt_lat <= lat + radius_deg and lon - radius_deg <= apt_lon <= lon + radius_deg):
                continue

            frequencies = self.get_airport_frequencies(ident)
            if not frequencies:
                continue

            airports.append({
                "ident": ident,
                "name": airport["name"],
                "lat": apt_lat,
                "lon": apt_lon,
                "distance_nm": round(self._distance_nm(lat, lon, apt_lat, apt_lon), 1),
                "frequencies": frequencies,
            })

        airports.sort(key=lambda item: item["distance_nm"])
        return airports[:limit]

    def _get_nearby_airports_from_sqlite(self, lat, lon, sqlite_path, limit=6, radius_deg=1.5):
        if not sqlite_path or "path/to/db" in sqlite_path or not os.path.exists(sqlite_path):
            return []

        import sqlite3

        conn = None
        try:
            conn = sqlite3.connect(sqlite_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT ident, name, laty, lonx
                FROM airport
                WHERE laty BETWEEN ? AND ? AND lonx BETWEEN ? AND ?
                """,
                (lat - radius_deg, lat + radius_deg, lon - radius_deg, lon + radius_deg),
            )
            airports = []
            for ident, name, apt_lat, apt_lon in cursor.fetchall():
                frequencies = self.get_airport_frequencies(ident)
                if not frequencies:
                    continue
                distance_nm = self._distance_nm(lat, lon, apt_lat, apt_lon)
                airports.append({
                    "ident": ident,
                    "name": name,
                    "lat": apt_lat,
                    "lon": apt_lon,
                    "distance_nm": round(distance_nm, 1),
                    "frequencies": frequencies,
                })
            airports.sort(key=lambda item: item["distance_nm"])
            return airports[:limit]
        except Exception as e:
            print(f"AirportFrequencyService: Nearby airport query failed - {e}")
            return []
        finally:
            if conn:
                conn.close()

    def _distance_nm(self, lat1, lon1, lat2, lon2):
        r = 3440.065
        lat1r = math.radians(lat1)
        lon1r = math.radians(lon1)
        lat2r = math.radians(lat2)
        lon2r = math.radians(lon2)
        dlat = lat2r - lat1r
        dlon = lon2r - lon1r
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return r * c
