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
        self.pending_issue = None

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
            "landing_cleared": "cleared to land" in lower or "cleared for landing" in lower,
            "taxi_route": self._parse_taxi_route(text),
            "handoff_frequency": self._parse_frequency(lower),
            "timestamp": now,
            "text": text,
        }

        # Preserve previous clearances unless a new instruction supersedes them.
        for key, value in parsed.items():
            if value not in (None, "", [], False) or key in {"timestamp", "text"}:
                self.instructions[key] = value

        if "hold short" in lower:
            self.instructions["hold_short_runway"] = parsed["hold_short_runway"] or "runway"
            self.instructions["takeoff_cleared"] = False
        if "contact " in lower and parsed["handoff_frequency"]:
            self.instructions["handoff_frequency"] = parsed["handoff_frequency"]

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
        if now - self.issue_last_seen.get(issue["type"], 0) < self.issue_cooldown:
            return
        if now - self.last_llm_request < self.llm_timeout_cooldown:
            return

        self.issue_last_seen[issue["type"]] = now
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
        event_bus.emit("atc_broadcast", text)
        print(f"ATCMonitor: Proactive ATC triggered for {issue.get('type', 'unknown')}")

    def _detect_issue(self, context_snapshot):
        aircraft = context_snapshot.get("aircraft", {}) or {}
        atc_state = context_snapshot.get("atc_state", {}) or {}
        role = atc_state.get("current_controller", "") or ""
        now = time.time()
        if now - self.instructions.get("timestamp", 0) < 12:
            return None

        on_ground = bool(aircraft.get("on_ground", True))
        altitude = float(aircraft.get("altitude", 0) or 0)
        airspeed = float(aircraft.get("airspeed", 0) or 0)
        heading = float(aircraft.get("heading", 0) or 0)
        vertical_speed = float(aircraft.get("vs", 0) or 0)
        current_freq = self._round_freq(aircraft.get("com1_freq"))

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

        if not on_ground and altitude < 10000 and airspeed > 255 and "Center" not in role:
            return self._issue("below_10000_speed", aircraft, role, f"below 10000 ft at {airspeed:.0f} kt")

        handoff_freq = self._round_freq(self.instructions.get("handoff_frequency"))
        if handoff_freq and current_freq and abs(current_freq - handoff_freq) >= 0.01 and now - self.instructions.get("timestamp", 0) > 45:
            return self._issue("handoff_not_completed", aircraft, role, f"instructed contact {handoff_freq:.3f}, current COM1 {current_freq:.3f}")

        if on_ground and "Ground" in role and self.instructions.get("hold_short_runway") and not self.instructions.get("takeoff_cleared"):
            # Without exact runway geometry here, use speed as a practical cue: if the pilot
            # accelerates after hold-short, ask AI whether to intervene.
            if airspeed > 35:
                return self._issue("hold_short_possible_violation", aircraft, role, f"hold short {self.instructions.get('hold_short_runway')} but aircraft accelerating")

        if not on_ground and altitude < 800 and "Tower" in role and not self.instructions.get("landing_cleared") and vertical_speed < -150:
            return self._issue("landing_clearance_missing", aircraft, role, f"short final below 800 ft without tracked landing clearance")

        return None

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
