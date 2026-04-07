"""
GroundDataService - selects airport ground layout source from simulator or OSM.
"""
from __future__ import annotations

from .msfs_ground_service import MSFSGroundService
from .osm_ground_service import OSMGroundService
from .simulator_ground_service import SimulatorGroundService


class GroundDataService:
    def __init__(self, config: dict, airport_frequency_service=None):
        self.config = config or {}
        self.airport_frequency_service = airport_frequency_service
        self.simulator_service = SimulatorGroundService(self.config)
        self.msfs_service = MSFSGroundService(self.config)
        self.osm_service = OSMGroundService(self.config, airport_lookup=self._lookup_airport)

    def update_config(self, config: dict):
        self.config = config or {}
        self.simulator_service.update_config(self.config)
        self.msfs_service.update_config(self.config)
        self.osm_service.update_config(self.config)

    def get_ground_source(self) -> str:
        navdata = self.config.get("navdata", {}) or {}
        return (navdata.get("ground_source") or "simulator").lower()

    def get_airport_layout(self, airport_icao: str):
        source = self.get_ground_source()
        simulator_provider = ((self.config.get("simulator", {}) or {}).get("provider") or "auto").lower()
        if source == "osm":
            layout = self.osm_service.get_airport_layout(airport_icao)
            if layout:
                return layout

        if simulator_provider in {"msfs", "p3d", "fsx"}:
            layout = self.msfs_service.get_airport_layout(airport_icao)
        else:
            layout = self.simulator_service.get_airport_layout(airport_icao)
        if layout:
            return layout
        if source != "osm":
            return self.osm_service.get_airport_layout(airport_icao)
        return None

    def get_nearest_airport(self, lat: float, lon: float):
        nearest = self.simulator_service.get_nearest_airport(lat, lon)
        if nearest:
            return nearest
        if self.airport_frequency_service:
            return self.airport_frequency_service.get_nearest_airport_ident(lat, lon)
        return None

    def _lookup_airport(self, airport_icao: str):
        if not self.airport_frequency_service:
            return None
        return self.airport_frequency_service.get_airport_position(airport_icao)
