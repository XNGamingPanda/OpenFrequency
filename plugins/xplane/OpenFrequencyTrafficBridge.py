"""
OpenFrequencyTrafficBridge

XPPython3 plugin that pulls self-managed traffic from OpenFrequency and publishes
it into X-Plane via the official TCAS override interface.

Install:
1. Install XPPython3 in X-Plane.
2. Copy this file into:
   Resources/plugins/PythonPlugins/OpenFrequencyTrafficBridge.py
3. Ensure OpenFrequency is running locally.
"""
from __future__ import annotations

import json
import math
import urllib.request

from XPPython3 import xp


class PythonInterface:
    PLUGIN_NAME = "OpenFrequency Traffic Bridge"
    PLUGIN_SIG = "com.opengrequency.xplane.trafficbridge"
    PLUGIN_DESC = "Publishes OpenFrequency self-managed traffic via official TCAS override."

    FETCH_INTERVAL = 1.0
    FLIGHT_LOOP_INTERVAL = -1.0
    MAX_TARGETS = 63

    def XPluginStart(self):
        self.targets = []
        self.last_fetch_ts = 0.0
        self.bridge_url = "http://127.0.0.1:5000/api/xplane/traffic_targets?limit=63"
        self.plugin_owns_planes = False
        self.datarefs = {}
        return self.PLUGIN_NAME, self.PLUGIN_SIG, self.PLUGIN_DESC

    def XPluginStop(self):
        self._release_planes()

    def XPluginEnable(self):
        self._find_datarefs()
        if not self._acquire_planes():
            xp.log("OpenFrequencyTrafficBridge: Could not acquire AI planes yet.")
        xp.registerFlightLoopCallback(self._flight_loop_cb, self.FLIGHT_LOOP_INTERVAL, None)
        return 1

    def XPluginDisable(self):
        xp.unregisterFlightLoopCallback(self._flight_loop_cb, None)
        self._release_planes()

    def XPluginReceiveMessage(self, inFromWho, inMessage, inParam):
        if inMessage == xp.MSG_RELEASE_PLANES:
            self._release_planes()

    def _find_datarefs(self):
        self.datarefs["user_lat"] = xp.findDataRef("sim/flightmodel/position/latitude")
        self.datarefs["user_lon"] = xp.findDataRef("sim/flightmodel/position/longitude")
        self.datarefs["user_elev_m"] = xp.findDataRef("sim/flightmodel/position/elevation")
        self.datarefs["user_true_psi"] = xp.findDataRef("sim/flightmodel/position/true_psi")
        self.datarefs["rel_bearing"] = xp.findDataRef("sim/cockpit2/tcas/indicators/relative_bearing_degs")
        self.datarefs["rel_distance"] = xp.findDataRef("sim/cockpit2/tcas/indicators/relative_distance_mtrs")
        self.datarefs["rel_altitude"] = xp.findDataRef("sim/cockpit2/tcas/indicators/relative_altitude_mtrs")
        self.datarefs["mode_s_id"] = xp.findDataRef("sim/cockpit2/tcas/targets/modeS_id")
        self.datarefs["flight_id"] = xp.findDataRef("sim/cockpit2/tcas/targets/flight_id")
        self.datarefs["override_tcas"] = xp.findDataRef("sim/operation/override/override_TCAS")

    def _acquire_planes(self):
        if self.plugin_owns_planes:
            return True
        if not xp.acquirePlanes(None, self._retry_acquiring_planes, None):
            return False
        xp.setDatai(self.datarefs["override_tcas"], 1)
        self.plugin_owns_planes = True
        xp.log("OpenFrequencyTrafficBridge: Acquired AI planes and enabled override_TCAS.")
        return True

    def _retry_acquiring_planes(self, refcon):
        self._acquire_planes()

    def _release_planes(self):
        if not self.plugin_owns_planes:
            return
        try:
            xp.setDatai(self.datarefs["override_tcas"], 0)
        except Exception:
            pass
        try:
            xp.releasePlanes()
        except Exception:
            pass
        self.plugin_owns_planes = False

    def _flight_loop_cb(self, elapsedSinceLastCall, elapsedTimeSinceLastFlightLoop, counter, refcon):
        if not self.plugin_owns_planes:
            self._acquire_planes()

        if elapsedTimeSinceLastFlightLoop - self.last_fetch_ts >= self.FETCH_INTERVAL:
            self.targets = self._fetch_targets()
            self.last_fetch_ts = elapsedTimeSinceLastFlightLoop

        self._publish_targets()
        return self.FLIGHT_LOOP_INTERVAL

    def _fetch_targets(self):
        try:
            with urllib.request.urlopen(self.bridge_url, timeout=0.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return payload.get("targets", [])[: self.MAX_TARGETS]
        except Exception as exc:
            xp.log(f"OpenFrequencyTrafficBridge: Fetch failed - {exc}")
            return []

    def _publish_targets(self):
        if not self.plugin_owns_planes:
            return

        target_count = min(len(self.targets), self.MAX_TARGETS)
        xp.setActiveAircraftCount(target_count)

        ids = [0] * 64
        bearings = [0.0] * 64
        distances = [0.0] * 64
        altitudes = [0.0] * 64
        flight_ids = bytearray(64 * 8)

        own_lat = xp.getDataf(self.datarefs["user_lat"])
        own_lon = xp.getDataf(self.datarefs["user_lon"])
        own_alt_m = xp.getDataf(self.datarefs["user_elev_m"])
        own_hdg = xp.getDataf(self.datarefs["user_true_psi"])

        for slot, target in enumerate(self.targets[:target_count], start=1):
            ids[slot] = int(target.get("mode_s_id", slot))
            rel_bearing, distance_m = self._relative_bearing_and_distance(
                own_lat,
                own_lon,
                own_hdg,
                float(target.get("latitude", own_lat)),
                float(target.get("longitude", own_lon)),
            )
            altitudes[slot] = (float(target.get("altitude_ft", 0.0)) * 0.3048) - own_alt_m
            bearings[slot] = rel_bearing
            distances[slot] = distance_m

            flight_id = (target.get("flight_id") or "")[:7].encode("ascii", errors="ignore")
            offset = slot * 8
            flight_ids[offset : offset + len(flight_id)] = flight_id

        xp.setDatavi(self.datarefs["mode_s_id"], ids, 0, 64)
        xp.setDatab(self.datarefs["flight_id"], flight_ids, 0, len(flight_ids))
        xp.setDatavf(self.datarefs["rel_bearing"], bearings, 0, 64)
        xp.setDatavf(self.datarefs["rel_distance"], distances, 0, 64)
        xp.setDatavf(self.datarefs["rel_altitude"], altitudes, 0, 64)

    @staticmethod
    def _relative_bearing_and_distance(own_lat, own_lon, own_hdg, tgt_lat, tgt_lon):
        distance_m = PythonInterface._distance_m(own_lat, own_lon, tgt_lat, tgt_lon)
        bearing = PythonInterface._bearing_deg(own_lat, own_lon, tgt_lat, tgt_lon)
        relative = (bearing - own_hdg + 540.0) % 360.0 - 180.0
        return relative, distance_m

    @staticmethod
    def _distance_m(lat1, lon1, lat2, lon2):
        radius_m = 6371000.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return 2 * radius_m * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def _bearing_deg(lat1, lon1, lat2, lon2):
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dlambda = math.radians(lon2 - lon1)
        x = math.sin(dlambda) * math.cos(phi2)
        y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
        return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0
