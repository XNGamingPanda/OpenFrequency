"""
SimulatorGroundService - airport ground/layout data from simulator-native files.

X-Plane uses apt.dat as the primary source of airport layout data, including
runways, startup locations, taxi routing nodes, and taxi edges. This service
indexes apt.dat and exposes a lightweight nearest-airport lookup plus
on-demand airport layout parsing.
"""
from __future__ import annotations

import math
import os
from typing import Dict, List, Optional

import psutil


class SimulatorGroundService:
    def __init__(self, config: dict):
        self.config = config or {}
        self._xplane_index = None
        self._xplane_layout_cache = {}
        self._xplane_files = []
        self._xplane_atc_frequency_index = None
        self._frequency_role_map = {
            "50": "ATIS",
            "51": "Unicom",
            "52": "Clearance Delivery",
            "53": "Ground",
            "54": "Tower",
            "55": "Approach",
            "56": "Departure",
        }

    def update_config(self, config: dict):
        self.config = config or {}
        self._xplane_index = None
        self._xplane_layout_cache = {}
        self._xplane_files = []
        self._xplane_atc_frequency_index = None

    def get_nearest_airport(self, lat: float, lon: float) -> Optional[str]:
        provider = self._effective_provider()
        if provider == "xplane":
            return self._get_nearest_xplane_airport(lat, lon)
        return None

    def get_airport_layout(self, airport_icao: str) -> Optional[dict]:
        provider = self._effective_provider()
        if provider == "xplane":
            return self._get_xplane_airport_layout(airport_icao)
        return None

    def get_airport_frequencies(self, airport_icao: str) -> List[dict]:
        layout = self.get_airport_layout(airport_icao)
        frequencies = [dict(item) for item in (layout.get("frequencies", []) if layout else [])]
        if frequencies:
            return frequencies
        return self._get_xplane_atc_frequencies(airport_icao)

    def _effective_provider(self) -> str:
        sim_config = self.config.get("simulator", {}) or {}
        provider = (sim_config.get("provider") or "auto").lower()
        proc_names = {p.info.get("name", "").lower() for p in psutil.process_iter(["name"])}
        if any(name in proc_names for name in ("x-plane.exe", "x-plane-x86_64.exe", "x-plane-arm64.exe")):
            return "xplane"
        return provider

    def _get_nearest_xplane_airport(self, lat: float, lon: float) -> Optional[str]:
        self._ensure_xplane_index()
        if not self._xplane_index:
            return None

        closest_ident = None
        closest_distance = None
        for ident, airport in self._xplane_index.items():
            apt_lat = airport.get("lat")
            apt_lon = airport.get("lon")
            if apt_lat is None or apt_lon is None:
                continue

            d2 = (apt_lat - lat) ** 2 + (apt_lon - lon) ** 2
            if closest_distance is None or d2 < closest_distance:
                closest_distance = d2
                closest_ident = ident

        return closest_ident

    def _ensure_xplane_index(self):
        if self._xplane_index is not None:
            return

        self._xplane_files = self._discover_xplane_apt_files()
        if not self._xplane_files:
            print("SimulatorGroundService: No X-Plane apt.dat files found.")
            self._xplane_index = {}
            return

        merged_index = {}
        for apt_path in self._xplane_files:
            try:
                file_index = self._build_xplane_index_for_file(apt_path)
                merged_index.update(file_index)
                print(f"SimulatorGroundService: Indexed {len(file_index)} airports from {apt_path}")
            except Exception as e:
                print(f"SimulatorGroundService: Failed to index {apt_path} - {e}")

        self._xplane_index = merged_index

    def _discover_xplane_apt_files(self) -> List[str]:
        sim_config = self.config.get("simulator", {}) or {}
        explicit_path = sim_config.get("xplane_apt_dat_path")
        if explicit_path and os.path.exists(explicit_path):
            return [explicit_path]

        root = sim_config.get("xplane_root") or self._detect_xplane_root()
        if not root or not os.path.isdir(root):
            return []

        default_apt = os.path.join(
            root, "Resources", "default scenery", "default apt dat", "Earth nav data", "apt.dat"
        )
        global_apt = os.path.join(
            root, "Global Scenery", "Global Airports", "Earth nav data", "apt.dat"
        )

        files = []
        for candidate in (default_apt, global_apt):
            if os.path.exists(candidate):
                files.append(candidate)

        custom_root = os.path.join(root, "Custom Scenery")
        custom_files = []
        if os.path.isdir(custom_root):
            for dirpath, _, filenames in os.walk(custom_root):
                if "apt.dat" in filenames:
                    custom_files.append(os.path.join(dirpath, "apt.dat"))
        custom_files.sort()
        files.extend(custom_files)
        return files

    def _detect_xplane_root(self) -> Optional[str]:
        sim_config = self.config.get("simulator", {}) or {}
        configured_host = sim_config.get("xplane_host")
        _ = configured_host  # Silence lint-style unused warning in plain Python.
        process_names = {"x-plane.exe", "x-plane-x86_64.exe", "x-plane-arm64.exe"}
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                name = (proc.info.get("name") or "").lower()
                exe = proc.info.get("exe")
                if name in process_names and exe:
                    return os.path.dirname(exe)
            except (psutil.Error, OSError):
                continue

        common_paths = [
            r"D:\SteamLibrary\steamapps\common\X-Plane 12",
            r"C:\X-Plane 12",
            r"C:\Program Files\X-Plane 12",
            r"C:\Program Files (x86)\Steam\steamapps\common\X-Plane 12",
        ]
        for path in common_paths:
            if os.path.isdir(path):
                return path
        return None

    def _discover_xplane_atc_file(self) -> Optional[str]:
        sim_config = self.config.get("simulator", {}) or {}
        root = sim_config.get("xplane_root") or self._detect_xplane_root()
        if not root or not os.path.isdir(root):
            return None
        atc_path = os.path.join(root, "Resources", "default scenery", "1200 atc data", "Earth nav data", "atc.dat")
        return atc_path if os.path.exists(atc_path) else None

    def _get_xplane_atc_frequencies(self, airport_icao: str) -> List[dict]:
        if self._xplane_atc_frequency_index is None:
            self._xplane_atc_frequency_index = self._build_xplane_atc_frequency_index()
        return [dict(item) for item in self._xplane_atc_frequency_index.get((airport_icao or "").upper(), [])]

    def _build_xplane_atc_frequency_index(self) -> Dict[str, List[dict]]:
        atc_path = self._discover_xplane_atc_file()
        if not atc_path:
            return {}

        role_map = {
            "twr": "Tower",
            "tracon": "Approach",
            "app": "Approach",
            "dep": "Departure",
            "gnd": "Ground",
            "del": "Clearance Delivery",
            "delivery": "Clearance Delivery",
            "ctr": "Center",
            "atis": "ATIS",
        }
        index = {}
        current = None
        with open(atc_path, "r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                if line == "CONTROLLER":
                    current = {"name": "", "ident": "", "role": "", "freqs": []}
                    continue
                if line == "CONTROLLER_END":
                    if current and current["ident"] and current["freqs"]:
                        ident = current["ident"].upper()
                        role = role_map.get(current["role"].lower(), current["role"].title() or "ATC")
                        entries = index.setdefault(ident, [])
                        for freq_raw in current["freqs"]:
                            frequency_mhz = round(float(freq_raw) / 100.0, 3)
                            description = f"{current['name']} {role}".strip()
                            entries.append(
                                {
                                    "airport_ident": ident,
                                    "type": role.upper(),
                                    "description": description,
                                    "frequency_mhz": frequency_mhz,
                                    "label": f"{description} {frequency_mhz:.3f}".strip(),
                                    "role": role,
                                    "source": "simulator",
                                }
                            )
                    current = None
                    continue
                if current is None:
                    continue
                if line.startswith("NAME "):
                    current["name"] = line[5:].strip()
                elif line.startswith("FACILITY_ID "):
                    current["ident"] = line[12:].strip()
                elif line.startswith("ROLE "):
                    current["role"] = line[5:].strip()
                elif line.startswith("FREQ "):
                    current["freqs"].append(line[5:].strip())

        for ident, entries in index.items():
            deduped = {}
            for entry in entries:
                key = (entry["role"], entry["frequency_mhz"])
                if key not in deduped:
                    deduped[key] = entry
            index[ident] = sorted(deduped.values(), key=lambda item: (item["frequency_mhz"], item["role"]))
        return index

    def _build_xplane_index_for_file(self, apt_path: str) -> Dict[str, dict]:
        index = {}
        current = None

        with open(apt_path, "r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue

                tokens = line.split()
                row_code = tokens[0]

                if row_code in {"1", "16", "17"}:
                    self._finalize_airport_index_entry(current, index, apt_path)
                    current = self._start_xplane_airport(tokens, apt_path)
                    continue

                if current is None:
                    continue

                if row_code == "1302" and len(tokens) >= 3:
                    key = tokens[1]
                    value = " ".join(tokens[2:])
                    if key == "datum_lat":
                        current["lat"] = self._safe_float(value)
                    elif key == "datum_lon":
                        current["lon"] = self._safe_float(value)
                    elif key == "country":
                        current["country"] = value
                    elif key == "city":
                        current["city"] = value
                    continue

                if row_code == "100" and len(tokens) >= 20:
                    lat1 = self._safe_float(tokens[9])
                    lon1 = self._safe_float(tokens[10])
                    lat2 = self._safe_float(tokens[18])
                    lon2 = self._safe_float(tokens[19])
                    current["sample_points"].append((lat1, lon1))
                    current["sample_points"].append((lat2, lon2))
                    continue

                if row_code == "102" and len(tokens) >= 4:
                    current["sample_points"].append((self._safe_float(tokens[2]), self._safe_float(tokens[3])))

        self._finalize_airport_index_entry(current, index, apt_path)
        return index

    def _start_xplane_airport(self, tokens: List[str], apt_path: str) -> dict:
        ident = tokens[4].upper() if len(tokens) >= 5 else "N/A"
        return {
            "ident": ident,
            "name": " ".join(tokens[5:]).strip() if len(tokens) > 5 else ident,
            "type": tokens[0],
            "source_path": apt_path,
            "lat": None,
            "lon": None,
            "country": "",
            "city": "",
            "sample_points": [],
        }

    def _finalize_airport_index_entry(self, current: Optional[dict], index: Dict[str, dict], apt_path: str):
        if not current or not current.get("ident"):
            return

        if current.get("lat") is None or current.get("lon") is None:
            if current["sample_points"]:
                lat = sum(p[0] for p in current["sample_points"]) / len(current["sample_points"])
                lon = sum(p[1] for p in current["sample_points"]) / len(current["sample_points"])
                current["lat"] = lat
                current["lon"] = lon

        current.pop("sample_points", None)
        current["source_path"] = apt_path
        index[current["ident"]] = current

    def _get_xplane_airport_layout(self, airport_icao: str) -> Optional[dict]:
        airport_icao = (airport_icao or "").upper()
        if not airport_icao:
            return None

        cached = self._xplane_layout_cache.get(airport_icao)
        if cached is not None:
            return cached

        self._ensure_xplane_index()
        if not self._xplane_files:
            return None

        for apt_path in reversed(self._xplane_files):
            layout = self._parse_xplane_airport_layout_from_file(apt_path, airport_icao)
            if layout:
                self._xplane_layout_cache[airport_icao] = layout
                return layout

        self._xplane_layout_cache[airport_icao] = None
        return None

    def _parse_xplane_airport_layout_from_file(self, apt_path: str, airport_icao: str) -> Optional[dict]:
        current = None
        in_target = False
        last_startup = None

        with open(apt_path, "r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue

                tokens = line.split()
                row_code = tokens[0]

                if row_code in {"1", "16", "17"}:
                    if in_target and current:
                        return current

                    ident = tokens[4].upper() if len(tokens) >= 5 else ""
                    in_target = ident == airport_icao
                    if in_target:
                        current = {
                            "ident": ident,
                            "name": " ".join(tokens[5:]).strip() if len(tokens) > 5 else ident,
                            "source_path": apt_path,
                            "metadata": {},
                            "runways": [],
                            "helipads": [],
                            "frequencies": [],
                            "startup_locations": [],
                            "taxi_nodes": [],
                            "taxi_edges": [],
                        }
                    continue

                if not in_target or current is None:
                    continue

                if row_code == "1302" and len(tokens) >= 3:
                    current["metadata"][tokens[1]] = " ".join(tokens[2:])
                    continue

                if row_code in self._frequency_role_map and len(tokens) >= 2:
                    freq_raw = self._safe_float(tokens[1], 0.0)
                    if freq_raw > 0:
                        frequency_mhz = round(freq_raw / 100.0, 3)
                        role = self._frequency_role_map[row_code]
                        description = " ".join(tokens[2:]).strip() or f"{airport_icao} {role}"
                        current["frequencies"].append(
                            {
                                "airport_ident": airport_icao,
                                "type": role.upper(),
                                "description": description,
                                "frequency_mhz": frequency_mhz,
                                "label": f"{description} {frequency_mhz:.3f}".strip(),
                                "role": role,
                                "source": "simulator",
                            }
                        )
                    continue

                if row_code == "100" and len(tokens) >= 20:
                    current["runways"].append(
                        {
                            "width_m": self._safe_float(tokens[1]),
                            "name1": tokens[8],
                            "lat1": self._safe_float(tokens[9]),
                            "lon1": self._safe_float(tokens[10]),
                            "name2": tokens[17],
                            "lat2": self._safe_float(tokens[18]),
                            "lon2": self._safe_float(tokens[19]),
                        }
                    )
                    continue

                if row_code == "102" and len(tokens) >= 4:
                    current["helipads"].append(
                        {
                            "name": tokens[1],
                            "lat": self._safe_float(tokens[2]),
                            "lon": self._safe_float(tokens[3]),
                            "heading": self._safe_float(tokens[4]) if len(tokens) > 4 else 0.0,
                        }
                    )
                    continue

                if row_code == "1300" and len(tokens) >= 5:
                    last_startup = {
                        "lat": self._safe_float(tokens[1]),
                        "lon": self._safe_float(tokens[2]),
                        "heading": self._safe_float(tokens[3]),
                        "type": tokens[4],
                        "name": " ".join(tokens[5:]).strip(),
                    }
                    current["startup_locations"].append(last_startup)
                    continue

                if row_code == "1301" and len(tokens) >= 2 and last_startup is not None:
                    last_startup["gate_id"] = tokens[1]
                    last_startup["operation"] = " ".join(tokens[2:]).strip()
                    continue

                if row_code == "1201" and len(tokens) >= 5:
                    current["taxi_nodes"].append(
                        {
                            "lat": self._safe_float(tokens[1]),
                            "lon": self._safe_float(tokens[2]),
                            "usage": tokens[3],
                            "id": tokens[4],
                        }
                    )
                    continue

                if row_code == "1202" and len(tokens) >= 5:
                    current["taxi_edges"].append(
                        {
                            "start": tokens[1],
                            "end": tokens[2],
                            "direction": tokens[3],
                            "kind": tokens[4],
                            "name": " ".join(tokens[5:]).strip(),
                        }
                    )
                    continue

        if in_target and current:
            current["frequencies"].sort(key=lambda item: (item["frequency_mhz"], item["role"]))
        return current if in_target and current else None

    @staticmethod
    def _safe_float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius_nm = 3440.065
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)

        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return 2 * radius_nm * math.atan2(math.sqrt(a), math.sqrt(1 - a))
