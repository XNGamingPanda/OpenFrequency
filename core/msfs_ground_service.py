"""
MSFSGroundService - airport layout reader for MSFS scenery packages.

This service prefers extracted/developer XML airport definitions when present.
Compiled BGL packages are discovered so package ownership can be identified, but
without an external decoder they remain metadata-only and will fall back to OSM.
"""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional


class MSFSGroundService:
    def __init__(self, config: dict):
        self.config = config or {}
        self._package_cache = None
        self._layout_cache = {}

    def update_config(self, config: dict):
        self.config = config or {}
        self._package_cache = None
        self._layout_cache = {}

    def get_airport_layout(self, airport_icao: str) -> Optional[dict]:
        airport_icao = (airport_icao or "").strip().upper()
        if not airport_icao:
            return None

        if airport_icao in self._layout_cache:
            return self._layout_cache[airport_icao]

        for package in self._find_candidate_packages(airport_icao):
            xml_path = self._find_airport_xml(package["root"], airport_icao)
            if xml_path:
                layout = self._parse_airport_xml(xml_path, airport_icao, package)
                if layout:
                    self._layout_cache[airport_icao] = layout
                    return layout

        self._layout_cache[airport_icao] = None
        return None

    def _find_candidate_packages(self, airport_icao: str) -> List[dict]:
        packages = self._load_packages()
        candidates = []
        for package in packages:
            haystacks = [
                package.get("name", ""),
                package.get("title", ""),
                package.get("root", ""),
            ]
            haystacks.extend(package.get("files", []))
            joined = " ".join(haystacks).upper()
            if airport_icao in joined:
                candidates.append(package)
        return candidates or packages

    def _load_packages(self) -> List[dict]:
        if self._package_cache is not None:
            return self._package_cache

        package_roots = self._discover_package_roots()
        packages = []
        for root in package_roots:
            if not os.path.isdir(root):
                continue
            for entry in os.scandir(root):
                if not entry.is_dir():
                    continue
                package = self._read_package(entry.path)
                if package:
                    packages.append(package)
        self._package_cache = packages
        return packages

    def _discover_package_roots(self) -> List[str]:
        sim_config = self.config.get("simulator", {}) or {}
        roots = []
        configured = sim_config.get("msfs_packages_root")
        if configured:
            roots.append(configured)

        common = [
            r"D:\Games\Microsoft Flight Simulator Packages\Community",
            r"D:\Games\Microsoft Flight Simulator Packages\Official",
            r"D:\Games\MSFS2024_Map\Community",
            r"D:\Games\MSFS2024_Map\Community2024",
            r"D:\Games\MSFS2024_Map\Official2020",
            r"D:\Games\MSFS2024_Map\Official2024",
        ]
        for path in common:
            if path not in roots and os.path.isdir(path):
                roots.append(path)
        return roots

    def _read_package(self, package_root: str) -> Optional[dict]:
        layout_path = os.path.join(package_root, "layout.json")
        manifest_path = os.path.join(package_root, "manifest.json")
        if not os.path.exists(layout_path) and not os.path.exists(manifest_path):
            return None

        files = []
        if os.path.exists(layout_path):
            try:
                with open(layout_path, "r", encoding="utf-8") as handle:
                    layout = json.load(handle)
                files = [item.get("path", "") for item in layout.get("content", []) if item.get("path")]
            except Exception:
                files = []

        title = ""
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as handle:
                    manifest = json.load(handle)
                title = manifest.get("title", "") or manifest.get("package_version", "")
            except Exception:
                title = ""

        return {
            "root": package_root,
            "name": os.path.basename(package_root),
            "title": title,
            "files": files,
        }

    def _find_airport_xml(self, package_root: str, airport_icao: str) -> Optional[str]:
        for dirpath, _, filenames in os.walk(package_root):
            for filename in filenames:
                lower = filename.lower()
                if not lower.endswith(".xml"):
                    continue
                if airport_icao.lower() in lower or "airport" in lower:
                    return os.path.join(dirpath, filename)
        return None

    def _parse_airport_xml(self, xml_path: str, airport_icao: str, package: dict) -> Optional[dict]:
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
        except Exception as e:
            print(f"MSFSGroundService: Failed to parse XML {xml_path} - {e}")
            return None

        airport_elem = None
        for elem in root.iter():
            if self._tag_name(elem.tag) == "Airport":
                ident = (elem.attrib.get("ident") or elem.attrib.get("icao") or "").strip().upper()
                if not ident or ident == airport_icao:
                    airport_elem = elem
                    break
        if airport_elem is None:
            return None

        layout = {
            "ident": airport_icao,
            "name": package.get("title") or package.get("name") or airport_icao,
            "source_path": xml_path,
            "metadata": {
                "source": "msfs_xml",
                "package_root": package.get("root"),
                "package_name": package.get("name"),
            },
            "runways": [],
            "helipads": [],
            "frequencies": [],
            "startup_locations": [],
            "taxi_nodes": [],
            "taxi_edges": [],
            "aprons": [],
        }

        node_map = {}
        for elem in airport_elem.iter():
            tag = self._tag_name(elem.tag)
            if tag == "TaxiwayPoint":
                point_id = elem.attrib.get("index") or elem.attrib.get("id") or elem.attrib.get("name")
                if not point_id:
                    continue
                node = {
                    "id": str(point_id),
                    "lat": self._to_float(elem.attrib.get("lat")),
                    "lon": self._to_float(elem.attrib.get("lon")),
                    "usage": (elem.attrib.get("type") or "both").lower(),
                }
                node_map[node["id"]] = node
            elif tag == "TaxiwayParking":
                lat = self._to_float(elem.attrib.get("lat"))
                lon = self._to_float(elem.attrib.get("lon"))
                stand_name = " ".join(filter(None, [elem.attrib.get("name"), elem.attrib.get("number")])).strip() or elem.attrib.get("type") or "Parking"
                layout["startup_locations"].append(
                    {
                        "lat": lat,
                        "lon": lon,
                        "heading": self._to_float(elem.attrib.get("heading"), 0.0),
                        "type": elem.attrib.get("type", "parking"),
                        "name": stand_name,
                        "gate_id": stand_name,
                        "operation": elem.attrib.get("airlineCodes", ""),
                    }
                )
            elif tag == "TaxiwayPath":
                start = elem.attrib.get("start") or elem.attrib.get("startPoint")
                end = elem.attrib.get("end") or elem.attrib.get("endPoint")
                if not start or not end:
                    continue
                path_type = (elem.attrib.get("type") or "TAXI").lower()
                name = elem.attrib.get("name") or elem.attrib.get("designator") or elem.attrib.get("number") or path_type
                layout["taxi_edges"].append(
                    {
                        "start": str(start),
                        "end": str(end),
                        "direction": "twoway" if elem.attrib.get("oneWay", "FALSE").upper() != "TRUE" else "oneway",
                        "kind": self._map_path_type(path_type),
                        "name": name,
                        "width_m": self._to_float(elem.attrib.get("width"), 0.0),
                        "surface": elem.attrib.get("surface", ""),
                    }
                )
            elif tag == "Runway":
                primary = elem.attrib.get("primaryDesignator") or elem.attrib.get("number") or "RWY"
                secondary = elem.attrib.get("secondaryDesignator") or elem.attrib.get("secondaryNumber") or "RWY"
                layout["runways"].append(
                    {
                        "width_m": self._to_float(elem.attrib.get("width"), 45.0),
                        "name1": primary,
                        "lat1": self._to_float(elem.attrib.get("lat")),
                        "lon1": self._to_float(elem.attrib.get("lon")),
                        "name2": secondary,
                        "lat2": self._to_float(elem.attrib.get("lat")),  # XML often stores center point only
                        "lon2": self._to_float(elem.attrib.get("lon")),
                    }
                )
            elif tag == "Apron":
                points = []
                for child in elem:
                    if self._tag_name(child.tag) in {"Vertex", "Point"}:
                        points.append((self._to_float(child.attrib.get("lat")), self._to_float(child.attrib.get("lon"))))
                if points:
                    layout["aprons"].append({"name": elem.attrib.get("name", "Apron"), "points": points})

        layout["taxi_nodes"] = list(node_map.values())
        self._attach_stands_to_nodes(layout)
        return layout if layout["taxi_nodes"] or layout["startup_locations"] else None

    def _attach_stands_to_nodes(self, layout: dict):
        taxi_nodes = list(layout.get("taxi_nodes", []))
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
            nearest = min(
                taxi_nodes,
                key=lambda node: self._distance_m(stand["lat"], stand["lon"], node["lat"], node["lon"]),
            )
            layout["taxi_edges"].append(
                {
                    "start": stand_id,
                    "end": nearest["id"],
                    "direction": "twoway",
                    "kind": "apron_link",
                    "name": stand.get("gate_id") or stand.get("name") or "Apron",
                    "width_m": 18.0,
                    "surface": "paved",
                }
            )

    @staticmethod
    def _tag_name(tag: str) -> str:
        return tag.split("}", 1)[-1] if "}" in tag else tag

    @staticmethod
    def _map_path_type(path_type: str) -> str:
        if "runway" in path_type:
            return "runway"
        if "parking" in path_type or "apron" in path_type:
            return "apron_link"
        return "taxiway"

    @staticmethod
    def _to_float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        from math import atan2, cos, radians, sin, sqrt

        radius_m = 6371000.0
        phi1 = radians(lat1)
        phi2 = radians(lat2)
        dphi = radians(lat2 - lat1)
        dlambda = radians(lon2 - lon1)
        a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
        return 2 * radius_m * atan2(sqrt(a), sqrt(1 - a))
