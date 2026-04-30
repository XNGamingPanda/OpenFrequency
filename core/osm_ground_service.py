"""
OSMGroundService - fetch airport ground layouts from OpenStreetMap via Overpass.
"""
from __future__ import annotations

import json
import math
import os
from typing import Callable, Optional

import requests


class OSMGroundService:
    def __init__(self, config: dict, airport_lookup: Optional[Callable[[str], Optional[dict]]] = None):
        self.config = config or {}
        self.airport_lookup = airport_lookup
        from .paths import writable_data_path
        self.cache_dir = writable_data_path("ground_cache", "osm")

    def update_config(self, config: dict):
        self.config = config or {}

    def get_airport_layout(self, airport_icao: str) -> Optional[dict]:
        airport_icao = (airport_icao or "").strip().upper()
        if not airport_icao:
            return None

        cache_path = os.path.join(self.cache_dir, f"{airport_icao}.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as handle:
                    return json.load(handle)
            except Exception:
                pass

        airport = self._lookup_airport(airport_icao)
        if not airport:
            return None

        payload = self._fetch_osm_payload(airport_icao, airport["lat"], airport["lon"])
        if not payload:
            return None

        layout = self._build_layout(airport_icao, airport, payload)
        if layout:
            with open(cache_path, "w", encoding="utf-8") as handle:
                json.dump(layout, handle, ensure_ascii=False, indent=2)
        return layout

    def _lookup_airport(self, airport_icao: str) -> Optional[dict]:
        if not self.airport_lookup:
            return None
        airport = self.airport_lookup(airport_icao)
        if not airport:
            return None
        lat = airport.get("lat")
        lon = airport.get("lon")
        if lat is None or lon is None:
            return None
        return airport

    def _fetch_osm_payload(self, airport_icao: str, lat: float, lon: float) -> Optional[dict]:
        navdata = self.config.get("navdata", {}) or {}
        endpoint = navdata.get("osm_overpass_url", "https://overpass-api.de/api/interpreter")
        radius_m = int(navdata.get("osm_radius_m", 5000) or 5000)
        query = f"""
[out:json][timeout:25];
(
  way["aeroway"~"taxiway|taxiway_link|runway|apron"](around:{radius_m},{lat},{lon});
  node["aeroway"~"parking_position|holding_position|gate"](around:{radius_m},{lat},{lon});
  way["aeroway"="parking_position"](around:{radius_m},{lat},{lon});
);
(._;>;);
out body;
"""
        try:
            resp = requests.post(endpoint, data={"data": query}, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"OSMGroundService: Failed to fetch OSM layout for {airport_icao} - {e}")
            return None

    def _build_layout(self, airport_icao: str, airport: dict, payload: dict) -> Optional[dict]:
        elements = payload.get("elements", [])
        nodes_by_id = {}
        ways = []
        for element in elements:
            if element.get("type") == "node":
                nodes_by_id[element["id"]] = element
            elif element.get("type") == "way":
                ways.append(element)

        layout = {
            "ident": airport_icao,
            "name": airport.get("name") or airport_icao,
            "source_path": "OpenStreetMap",
            "metadata": {"source": "osm", "datum_lat": airport["lat"], "datum_lon": airport["lon"]},
            "runways": [],
            "helipads": [],
            "frequencies": [],
            "startup_locations": [],
            "taxi_nodes": [],
            "taxi_edges": [],
            "aprons": [],
        }

        seen_nodes = set()
        taxi_node_map = {}

        for way in ways:
            tags = way.get("tags", {})
            aeroway = tags.get("aeroway", "")
            node_ids = way.get("nodes", [])
            if aeroway in {"taxiway", "taxiway_link", "runway"}:
                ref_name = tags.get("ref") or tags.get("name") or tags.get("local_ref") or aeroway
                for node_id in node_ids:
                    node = nodes_by_id.get(node_id)
                    if not node:
                        continue
                    taxi_node_map[str(node_id)] = {
                        "id": str(node_id),
                        "lat": node["lat"],
                        "lon": node["lon"],
                        "usage": "both",
                    }
                for start_id, end_id in zip(node_ids, node_ids[1:]):
                    if start_id not in nodes_by_id or end_id not in nodes_by_id:
                        continue
                    layout["taxi_edges"].append(
                        {
                            "start": str(start_id),
                            "end": str(end_id),
                            "direction": "twoway" if tags.get("oneway") != "yes" else "oneway",
                            "kind": aeroway,
                            "name": ref_name,
                            "width_m": self._safe_float(tags.get("width")),
                            "surface": tags.get("surface", ""),
                        }
                    )
                if aeroway == "runway" and len(node_ids) >= 2:
                    first_node = nodes_by_id.get(node_ids[0])
                    last_node = nodes_by_id.get(node_ids[-1])
                    if first_node and last_node:
                        layout["runways"].append(
                            {
                                "width_m": self._safe_float(tags.get("width"), 45.0),
                                "name1": (ref_name.split("/") + ["RWY"])[0],
                                "lat1": first_node["lat"],
                                "lon1": first_node["lon"],
                                "name2": (ref_name.split("/") + ["RWY", "RWY"])[1],
                                "lat2": last_node["lat"],
                                "lon2": last_node["lon"],
                            }
                        )
            elif aeroway == "apron":
                coords = []
                for node_id in node_ids:
                    node = nodes_by_id.get(node_id)
                    if node:
                        coords.append((node["lat"], node["lon"]))
                if coords:
                    layout["aprons"].append({"name": tags.get("name") or tags.get("ref") or "Apron", "points": coords})
            elif aeroway == "parking_position":
                point = self._way_centroid(node_ids, nodes_by_id)
                if point:
                    layout["startup_locations"].append(
                        {
                            "lat": point[0],
                            "lon": point[1],
                            "heading": self._safe_float(tags.get("direction")),
                            "type": tags.get("parking", "parking_position"),
                            "name": tags.get("name") or tags.get("ref") or "Parking",
                            "gate_id": tags.get("ref") or tags.get("name") or "",
                            "operation": tags.get("parking", ""),
                        }
                    )

        for element in elements:
            if element.get("type") != "node":
                continue
            tags = element.get("tags", {})
            aeroway = tags.get("aeroway", "")
            node_id = str(element["id"])
            if aeroway in {"parking_position", "gate"}:
                layout["startup_locations"].append(
                    {
                        "lat": element["lat"],
                        "lon": element["lon"],
                        "heading": self._safe_float(tags.get("direction")),
                        "type": aeroway,
                        "name": tags.get("name") or tags.get("ref") or "Parking",
                        "gate_id": tags.get("ref") or tags.get("name") or "",
                        "operation": tags.get("parking", ""),
                    }
                )
            if aeroway == "holding_position":
                taxi_node_map[node_id] = {
                    "id": node_id,
                    "lat": element["lat"],
                    "lon": element["lon"],
                    "usage": "hold_short",
                }
                seen_nodes.add(node_id)

        for node_id, node in taxi_node_map.items():
            if node_id not in seen_nodes:
                layout["taxi_nodes"].append(node)
                seen_nodes.add(node_id)

        self._attach_parking_to_taxi_network(layout)
        return layout

    def _attach_parking_to_taxi_network(self, layout: dict):
        taxi_nodes = layout.get("taxi_nodes", [])
        if not taxi_nodes:
            return

        next_id = 1
        for stand in layout.get("startup_locations", []):
            stand_id = f"stand:{next_id}"
            next_id += 1
            layout["taxi_nodes"].append(
                {
                    "id": stand_id,
                    "lat": stand["lat"],
                    "lon": stand["lon"],
                    "usage": "gate",
                }
            )
            nearest_node = min(
                taxi_nodes,
                key=lambda node: self._distance_m(stand["lat"], stand["lon"], node["lat"], node["lon"]),
            )
            if self._distance_m(stand["lat"], stand["lon"], nearest_node["lat"], nearest_node["lon"]) <= 120:
                layout["taxi_edges"].append(
                    {
                        "start": stand_id,
                        "end": nearest_node["id"],
                        "direction": "twoway",
                        "kind": "apron_link",
                        "name": stand.get("gate_id") or stand.get("name") or "Apron",
                        "width_m": 18.0,
                        "surface": "paved",
                    }
                )

    @staticmethod
    def _way_centroid(node_ids, nodes_by_id):
        coords = [(nodes_by_id[node_id]["lat"], nodes_by_id[node_id]["lon"]) for node_id in node_ids if node_id in nodes_by_id]
        if not coords:
            return None
        return (
            sum(lat for lat, _ in coords) / len(coords),
            sum(lon for _, lon in coords) / len(coords),
        )

    @staticmethod
    def _safe_float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius_m = 6371000.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return 2 * radius_m * math.atan2(math.sqrt(a), math.sqrt(1 - a))
