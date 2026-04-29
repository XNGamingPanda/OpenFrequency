"""
ATCMonitor - watches aircraft state against recent ATC instructions.

The monitor uses local rules as a cheap pre-filter, then asks the LLM whether
ATC should speak. This avoids polling the LLM on every telemetry update.
"""
import json
import math
import re
import time

from .context import event_bus


class ATCMonitor:
    def __init__(self, config):
        self.config = config
        monitor_config = config.get("atc_monitor", {}) or {}
        self.enabled = monitor_config.get("enabled", True)
        self.check_interval = float(monitor_config.get("check_interval_sec", 8))
        self.global_cooldown = float(monitor_config.get("global_cooldown_sec", 35))
        self.issue_cooldown = float(monitor_config.get("issue_cooldown_sec", 120))
        self.llm_timeout_cooldown = float(monitor_config.get("llm_timeout_cooldown_sec", 20))

        self.last_check = 0.0
        self.last_llm_request = 0.0
        self.last_speak = 0.0
        self.issue_last_seen = {}
        self.issue_repeat_count = {}   # how many times each issue type has fired
        self.pending_issue = None

        # Track radar contact already given on this frequency (avoid repeat)
        self._radar_contact_given_freq: float | None = None
        # Track last known frequency for "first contact" detection
        self._last_known_freq: float | None = None
        # VFR pattern phase tracking
        self._vfr_pattern_phase: str = "unknown"   # unknown | upwind | crosswind | downwind | base | final

        self.instructions = {
            "altitude_ft": None,
            "heading_deg": None,
            "speed_kt": None,
            "hold_short_runway": None,
            "takeoff_cleared": False,
            "landing_cleared": False,
            "taxi_route": [],
            "handoff_frequency": None,
            "timestamp": 0.0,
            "text": "",
        }

    def start(self):
        if not self.enabled:
            print("ATCMonitor: Disabled")
            return
        event_bus.on("telemetry_update", self.on_telemetry_update)
        event_bus.on("atc_instruction_issued", self.on_atc_instruction)
        event_bus.on("atc_monitor_decision", self.on_llm_decision)
        event_bus.on("config_updated", self.on_config_update)
        print("ATCMonitor: Started")

    def on_config_update(self, config):
        self.config = config
        monitor_config = config.get("atc_monitor", {}) or {}
        self.enabled = monitor_config.get("enabled", True)
        self.check_interval = float(monitor_config.get("check_interval_sec", self.check_interval))
        self.global_cooldown = float(monitor_config.get("global_cooldown_sec", self.global_cooldown))
        self.issue_cooldown = float(monitor_config.get("issue_cooldown_sec", self.issue_cooldown))

    def on_atc_instruction(self, text, action=None, context_snapshot=None):
        text = (text or "").strip()
        if not text:
            return

        lower = text.lower()
        now = time.time()
        parsed = {
            "altitude_ft": self._parse_altitude(lower),
            "heading_deg": self._parse_heading(lower),
            "speed_kt": self._parse_speed(lower),
            "hold_short_runway": self._parse_hold_short(lower),
            "takeoff_cleared": "cleared for takeoff" in lower,
            "landing_cleared": (
                "cleared to land" in lower
                or "cleared for landing" in lower
                or "cleared touch-and-go" in lower
                or "cleared for touch" in lower
            ),
            "taxi_route": self._parse_taxi_route(text),
            "handoff_frequency": self._parse_frequency(lower),
            "timestamp": now,
            "text": text,
        }

        # New instruction resets repeat counters so the monitor can fire again on fresh issues
        self.issue_repeat_count.clear()

        # Preserve previous clearances unless a new instruction supersedes them.
        for key, value in parsed.items():
            if value not in (None, "", [], False) or key in {"timestamp", "text"}:
                self.instructions[key] = value

        if "hold short" in lower:
            self.instructions["hold_short_runway"] = parsed["hold_short_runway"] or "runway"
            self.instructions["takeoff_cleared"] = False
        if "contact " in lower and parsed["handoff_frequency"]:
            self.instructions["handoff_frequency"] = parsed["handoff_frequency"]

        # If "radar contact" was said by ATC, mark this frequency as having received it
        if "radar contact" in lower:
            freq = parsed.get("handoff_frequency") or self._last_known_freq
            self._radar_contact_given_freq = freq

    def on_telemetry_update(self, context_snapshot):
        if not self.enabled:
            return
        now = time.time()
        if now - self.last_check < self.check_interval:
            return
        self.last_check = now

        issue = self._detect_issue(context_snapshot)
        if not issue:
            return
        itype = issue["type"]
        if now - self.issue_last_seen.get(itype, 0) < self.issue_cooldown:
            return
        if now - self.last_llm_request < self.llm_timeout_cooldown:
            return
        # Limit repeats per issue type
        _max_repeats_map = {
            "handoff_not_completed":      2,
            "radar_contact_initial":      1,   # Only fire once per frequency
            "vfr_base_final_no_clearance":2,
            "vfr_pattern_high_departure": 1,
            "traffic_proximity_conflict": 3,
        }
        max_repeats = _max_repeats_map.get(itype, 8)
        if self.issue_repeat_count.get(itype, 0) >= max_repeats:
            return

        self.issue_last_seen[itype] = now
        self.issue_repeat_count[itype] = self.issue_repeat_count.get(itype, 0) + 1
        self.last_llm_request = now
        self.pending_issue = issue
        event_bus.emit("atc_monitor_check", issue, context_snapshot)

    def on_llm_decision(self, response_text, metadata=None):
        issue = (metadata or {}).get("issue") or self.pending_issue or {}
        text = self._parse_decision_text(response_text)
        if not text:
            return
        now = time.time()
        if now - self.last_speak < self.global_cooldown:
            return
        self.last_speak = now
        # If this was the initial radar contact call, mark the frequency as having received it
        if issue.get("type") == "radar_contact_initial" and self._last_known_freq:
            self._radar_contact_given_freq = self._last_known_freq
        event_bus.emit("atc_broadcast", text)
        print(f"ATCMonitor: Proactive ATC triggered for {issue.get('type', 'unknown')}")

    def _detect_issue(self, context_snapshot):
        aircraft = context_snapshot.get("aircraft", {}) or {}
        atc_state = context_snapshot.get("atc_state", {}) or {}
        role = atc_state.get("current_controller", "") or ""
        flight_rules = context_snapshot.get("flight_rules", "IFR")
        now = time.time()

        # Allow at least 12 s after a new ATC instruction before re-checking
        if now - self.instructions.get("timestamp", 0) < 12:
            return None

        on_ground = bool(aircraft.get("on_ground", True))
        altitude = float(aircraft.get("altitude", 0) or 0)
        airspeed = float(aircraft.get("airspeed", 0) or 0)
        heading = float(aircraft.get("heading", 0) or 0)
        vertical_speed = float(aircraft.get("vs", 0) or 0)
        current_freq = self._round_freq(aircraft.get("com1_freq"))

        # ── Track frequency changes for "radar contact" detection ─────────────
        if current_freq and current_freq != self._last_known_freq:
            self._last_known_freq = current_freq
            # Clear radar-contact flag on any frequency change so it can re-fire
            if current_freq != self._radar_contact_given_freq:
                self._radar_contact_given_freq = None

        # ── Initial "Radar Contact" call (VFR flight following) ───────────────
        # Fire once per new frequency when pilot is airborne and no radar contact yet given
        if (not on_ground
                and airspeed > 60
                and current_freq
                and self._radar_contact_given_freq != current_freq
                and now - self.instructions.get("timestamp", 0) > 30
                and now - self.issue_last_seen.get("radar_contact_initial", 0) > 600):
            # Only do this for roles that should give radar contact
            _radar_roles = ("Approach", "Departure", "Center", "Radar", "TRACON")
            if any(r in role for r in _radar_roles):
                return self._issue(
                    "radar_contact_initial", aircraft, role,
                    f"aircraft just tuned {current_freq:.3f}, no radar contact given yet on this frequency"
                )

        # ── IFR deviation checks ──────────────────────────────────────────────
        if flight_rules != "VFR":
            assigned_alt = self.instructions.get("altitude_ft")
            if assigned_alt and not on_ground:
                deviation = altitude - assigned_alt
                moving_away = (deviation > 400 and vertical_speed > 300) or (deviation < -400 and vertical_speed < -300)
                if abs(deviation) > 700 or moving_away:
                    return self._issue("altitude_deviation", aircraft, role, f"assigned {assigned_alt:.0f} ft, actual {altitude:.0f} ft")

            assigned_heading = self.instructions.get("heading_deg")
            if assigned_heading is not None and not on_ground and airspeed > 80:
                delta = abs((heading - assigned_heading + 180) % 360 - 180)
                if delta > 30:
                    return self._issue("heading_deviation", aircraft, role, f"assigned heading {assigned_heading:03.0f}, actual {heading:03.0f}, deviation {delta:.0f} degrees")

            assigned_speed = self.instructions.get("speed_kt")
            if assigned_speed and not on_ground and airspeed > 60:
                if abs(airspeed - assigned_speed) > 35:
                    return self._issue("speed_deviation", aircraft, role, f"assigned {assigned_speed:.0f} kt, actual {airspeed:.0f} kt")
        else:
            # ── VFR-specific checks ───────────────────────────────────────────

            # VFR traffic pattern monitoring (Tower only)
            if "Tower" in role and not on_ground:
                pattern_issue = self._detect_vfr_pattern_issue(aircraft, altitude, vertical_speed, airspeed)
                if pattern_issue:
                    return pattern_issue

        # ── Speed limit below 10,000 ft (both IFR and VFR) ───────────────────
        if not on_ground and altitude < 10000 and airspeed > 255 and "Center" not in role:
            return self._issue("below_10000_speed", aircraft, role, f"below 10000 ft at {airspeed:.0f} kt")

        # ── Handoff reminder ──────────────────────────────────────────────────
        handoff_freq = self._round_freq(self.instructions.get("handoff_frequency"))
        if handoff_freq and current_freq and abs(current_freq - handoff_freq) >= 0.01 and now - self.instructions.get("timestamp", 0) > 45:
            # Aircraft stopped on ground: pilot likely parked — clear stale handoff, don't nag
            if on_ground and airspeed < 5:
                self.instructions["handoff_frequency"] = None
            else:
                return self._issue("handoff_not_completed", aircraft, role, f"instructed contact {handoff_freq:.3f}, current COM1 {current_freq:.3f}")

        # ── Hold-short possible violation ─────────────────────────────────────
        if on_ground and "Ground" in role and self.instructions.get("hold_short_runway") and not self.instructions.get("takeoff_cleared"):
            if airspeed > 35:
                return self._issue("hold_short_possible_violation", aircraft, role, f"hold short {self.instructions.get('hold_short_runway')} but aircraft accelerating")

        # ── Landing clearance missing ─────────────────────────────────────────
        if not on_ground and altitude < 800 and "Tower" in role and not self.instructions.get("landing_cleared") and vertical_speed < -150:
            return self._issue("landing_clearance_missing", aircraft, role, f"short final below 800 ft without tracked landing clearance")

        # ── Traffic proximity conflict (both IFR and VFR) ─────────────────────
        proximity_issue = self._detect_traffic_conflict(context_snapshot, aircraft, altitude, on_ground)
        if proximity_issue:
            return proximity_issue

        return None

    def _detect_vfr_pattern_issue(self, aircraft, altitude, vertical_speed, airspeed):
        """
        Detect VFR traffic pattern anomalies. Returns an issue dict or None.
        Pattern altitude heuristic: traffic pattern is typically 600–1500 ft AGL.
        We use absolute altitude as a rough proxy (airport elevation unknown here).
        """
        role = "Tower"
        # Aircraft ascending through pattern altitude range with takeoff cleared
        if self.instructions.get("takeoff_cleared"):
            if altitude > 1800 and vertical_speed > 200:
                # Still climbing well above pattern — pilot may be departing (normal)
                # or forgot to enter the pattern
                if altitude > 3000 and vertical_speed > 300:
                    return self._issue(
                        "vfr_pattern_high_departure", aircraft, role,
                        f"aircraft climbing through {altitude:.0f} ft after takeoff clearance with make-traffic instruction — possible departure vs. pattern confusion"
                    )
        # No landing clearance while below 1200 ft and descending
        if (not self.instructions.get("landing_cleared")
                and altitude < 1200
                and vertical_speed < -200
                and airspeed > 60):
            return self._issue(
                "vfr_base_final_no_clearance", aircraft, role,
                f"aircraft descending through {altitude:.0f} ft at {vertical_speed:.0f} fpm, appears on base/final, no landing clearance issued"
            )
        return None

    def _detect_traffic_conflict(self, context_snapshot, aircraft, altitude, on_ground):
        """
        Warn if a known traffic target is within 5 NM and within 1000 ft vertically.
        Uses context_snapshot.environment.traffic_targets if available.
        """
        if on_ground:
            return None
        targets = context_snapshot.get("environment", {}).get("traffic_targets", []) or []
        if not targets:
            return None

        own_lat = float(aircraft.get("latitude", 0) or 0)
        own_lon = float(aircraft.get("longitude", 0) or 0)
        own_alt = altitude

        role = context_snapshot.get("atc_state", {}).get("current_controller", "ATC") or "ATC"
        # Only Tower/Approach/Departure should give traffic advisories
        _advisory_roles = ("Tower", "Approach", "Departure", "Center", "Radar")
        if not any(r in role for r in _advisory_roles):
            return None

        CONFLICT_NM = 5.0
        CONFLICT_ALT_FT = 1000.0
        now = time.time()

        for t in targets:
            try:
                t_lat = float(t.get("lat") or t.get("latitude", 0))
                t_lon = float(t.get("lon") or t.get("longitude", 0))
                t_alt = float(t.get("altitude", 0) or 0)
                t_call = str(t.get("callsign") or "traffic").strip()
            except Exception:
                continue

            if abs(t_alt - own_alt) > CONFLICT_ALT_FT:
                continue

            dist_nm = self._haversine_nm(own_lat, own_lon, t_lat, t_lon)
            if dist_nm < CONFLICT_NM:
                ikey = f"traffic_conflict_{t_call[:8]}"
                if now - self.issue_last_seen.get(ikey, 0) > 120:
                    self.issue_last_seen[ikey] = now
                    return self._issue(
                        "traffic_proximity_conflict",
                        aircraft, role,
                        f"{t_call} is {dist_nm:.1f} NM away at {t_alt:.0f} ft (own altitude {own_alt:.0f} ft)"
                    )
        return None

    @staticmethod
    def _haversine_nm(lat1, lon1, lat2, lon2) -> float:
        """Great-circle distance in nautical miles."""
        R = 3440.065  # Earth radius in NM
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _issue(self, issue_type, aircraft, role, detail, low_priority=False):
        return {
            "type": issue_type,
            "role": role,
            "detail": detail,
            "low_priority": low_priority,
            "instruction": self.instructions.get("text", ""),
            "aircraft": {
                "altitude": aircraft.get("altitude"),
                "heading": aircraft.get("heading"),
                "airspeed": aircraft.get("airspeed"),
                "vs": aircraft.get("vs"),
                "on_ground": aircraft.get("on_ground"),
                "com1_freq": aircraft.get("com1_freq"),
            },
        }

    def _parse_decision_text(self, response_text):
        raw = (response_text or "").strip()
        if not raw:
            return ""
        try:
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            data = json.loads(match.group(0) if match else raw)
            if str(data.get("action", "")).upper() in {"SILENT", "NONE"} and not data.get("text"):
                return ""
            return (data.get("text") or "").strip()
        except Exception:
            return raw if len(raw) < 240 else ""

    def _parse_altitude(self, text):
        match = re.search(r"(?:maintain|climb(?: and maintain)?|descend(?: and maintain)?|altitude)\s+(?:flight level\s*)?(\d{2,5})", text)
        if not match:
            match = re.search(r"\bfl\s?(\d{2,3})\b", text)
            if match:
                return float(match.group(1)) * 100
            return None
        value = float(match.group(1))
        if value < 600:
            return value * 100
        return value

    def _parse_heading(self, text):
        match = re.search(r"(?:heading|turn (?:left|right) heading|fly heading)\s+(\d{2,3})", text)
        return float(match.group(1)) % 360 if match else None

    def _parse_speed(self, text):
        match = re.search(r"(?:speed|maintain speed|reduce speed to|increase speed to)\s+(\d{2,3})", text)
        return float(match.group(1)) if match else None

    def _parse_hold_short(self, text):
        match = re.search(r"hold short(?: of)?(?: runway)?\s*([0-9]{1,2}[lrc]?)?", text)
        return (match.group(1) or "runway").upper() if match else None

    def _parse_frequency(self, text):
        match = re.search(r"\b(1[1-3][0-9]\.\d{2,3})\b", text)
        return float(match.group(1)) if match else None

    def _parse_taxi_route(self, text):
        lower = text.lower()
        if "taxi" not in lower:
            return []
        route = []
        for token in re.split(r"[, ]+", text):
            clean = token.strip().strip(".").upper()
            if re.fullmatch(r"[A-Z][0-9A-Z]?", clean):
                route.append(clean)
        return route[:12]

    @staticmethod
    def _round_freq(value):
        try:
            return round(float(value), 3)
        except Exception:
            return None
