"""
cpdlc_manager.py — Simplified CPDLC (Controller–Pilot Data Link Communications)

Implements a subset of ICAO Doc 9705 CPDLC for use with supported aircraft models
(e.g. FlyByWire A32NX, PMDG 737/777, Fenix A320, Toliss).

Responsibilities:
  • Maintain a MRN (Message Reference Number) counter
  • Compose downlink messages (pilot → ATC)
  • Accept and parse uplink messages (ATC → pilot via LLM)
  • Track open/closed message pairs (request → response)
  • Emit events on the shared EventBus so the UI and LLM can react
  • Expose a /cpdlc API surface for the Flask app

Message flow:
  1. Pilot sends a downlink (e.g. REQUEST CLIMB TO FL350)
  2. Manager stores it as pending, emits 'cpdlc_downlink' on event bus
  3. LLM picks it up via system prompt injection, responds in CPDLC format
  4. LLM response is fed to receive_uplink(); parsed and emitted as 'cpdlc_uplink'
  5. UI shows the exchange; pilot can ACK / WILCO / UNABLE / STANDBY
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from core.context import event_bus, shared_context, context_lock


# ── Enums / constants ─────────────────────────────────────────────────────────

class CpdlcStatus(str, Enum):
    PENDING   = "PENDING"    # sent, waiting for ATC response
    WILCO     = "WILCO"      # acknowledged, will comply
    ROGER     = "ROGER"      # acknowledged, no action required
    UNABLE    = "UNABLE"     # ATC cannot comply
    STANDBY   = "STANDBY"    # ATC needs time to respond
    ERROR     = "ERROR"      # malformed / timed-out
    CLOSED    = "CLOSED"     # exchange complete

class MessageDirection(str, Enum):
    DOWNLINK  = "D"          # pilot → ATC
    UPLINK    = "U"          # ATC  → pilot


# Standard CPDLC downlink element types (simplified)
DOWNLINK_TYPES = {
    "REQUEST_CLIMB":      "REQUEST CLIMB TO {level}",
    "REQUEST_DESCENT":    "REQUEST DESCENT TO {level}",
    "REQUEST_LEVEL":      "REQUEST LEVEL {level}",
    "REQUEST_OFFSET":     "REQUEST OFFSET {nm} NM {dir} OF ROUTE",
    "REQUEST_DIRECT":     "REQUEST DIRECT TO {waypoint}",
    "REQUEST_FREQ":       "REQUEST FREQUENCY CHANGE TO {freq}",
    "WILCO":              "WILCO",
    "UNABLE":             "UNABLE",
    "STANDBY":            "STANDBY",
    "ROGER":              "ROGER",
    "REQUEST_LOGON":      "REQUEST LOGON",
    "LOGOFF":             "LOGOFF",
    "POSITION_REPORT":    "POSITION {fix} AT {time} FL{level} NEXT {next_fix}",
    "FREE_TEXT":          "{text}",
}


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class CpdlcMessage:
    mrn:       int
    direction: MessageDirection
    text:      str
    status:    CpdlcStatus = CpdlcStatus.PENDING
    timestamp: float       = field(default_factory=time.time)
    response_mrn: Optional[int] = None   # uplink MRN that responded to this

    def to_dict(self) -> dict:
        return {
            "mrn":       self.mrn,
            "direction": self.direction.value,
            "text":      self.text,
            "status":    self.status.value,
            "timestamp": self.timestamp,
            "response_mrn": self.response_mrn,
        }


# ── Manager ───────────────────────────────────────────────────────────────────

class CpdlcManager:
    """
    Thread-safe CPDLC session manager.

    Usage:
        mgr = CpdlcManager()
        mrn = mgr.send_downlink("REQUEST_CLIMB", level="FL350")
        # LLM produces uplink text later:
        mgr.receive_uplink("WILCO [MRN/3]")
    """

    def __init__(self):
        self._lock      = threading.Lock()
        self._mrn_seq   = 0
        self._messages: dict[int, CpdlcMessage] = {}
        # Pending downlinks awaiting an uplink response
        self._pending_mrns: list[int] = []
        self.session_active = False
        self.logged_on_to:  Optional[str] = None   # facility identifier

        # Subscribe to LLM response events so we can parse CPDLC uplinks inline
        event_bus.on('cpdlc_uplink_raw', self._handle_uplink_raw)

    # ── MRN bookkeeping ───────────────────────────────────────────────────────

    def _next_mrn(self) -> int:
        self._mrn_seq = (self._mrn_seq % 63) + 1   # ICAO: 1–63 then wrap
        return self._mrn_seq

    # ── Session control ───────────────────────────────────────────────────────

    def logon(self, facility: str) -> int:
        """Initiate CPDLC logon to a facility.  Returns the downlink MRN."""
        with self._lock:
            self.session_active = True
            self.logged_on_to   = facility.upper()
        mrn = self._create_downlink("REQUEST LOGON")
        event_bus.emit('cpdlc_session_change', {
            "active": True, "facility": facility
        })
        return mrn

    def logoff(self) -> int:
        """Send LOGOFF and mark session inactive."""
        mrn = self._create_downlink("LOGOFF")
        with self._lock:
            self.session_active = False
            self.logged_on_to   = None
        event_bus.emit('cpdlc_session_change', {"active": False, "facility": None})
        return mrn

    # ── Downlinks (pilot → ATC) ───────────────────────────────────────────────

    def send_downlink(self, msg_type: str, **kwargs) -> int:
        """
        Compose and queue a downlink message.

        :param msg_type: key from DOWNLINK_TYPES or 'FREE_TEXT'
        :param kwargs:   template fill-ins (level, waypoint, text, etc.)
        :returns:        MRN assigned to this message
        """
        template = DOWNLINK_TYPES.get(msg_type, "{text}")
        try:
            text = template.format(**kwargs)
        except KeyError:
            text = template  # emit as-is if placeholders missing

        return self._create_downlink(text)

    def send_free_text(self, text: str) -> int:
        """Send an arbitrary free-text downlink."""
        return self._create_downlink(text.strip().upper())

    def _create_downlink(self, text: str) -> int:
        with self._lock:
            mrn = self._next_mrn()
            msg = CpdlcMessage(
                mrn=mrn,
                direction=MessageDirection.DOWNLINK,
                text=text,
            )
            self._messages[mrn] = msg
            self._pending_mrns.append(mrn)

        event_bus.emit('cpdlc_downlink', msg.to_dict())
        return mrn

    # ── Uplinks (ATC → pilot) ─────────────────────────────────────────────────

    def receive_uplink(self, text: str, responds_to_mrn: Optional[int] = None) -> CpdlcMessage:
        """
        Record an uplink message from ATC (generated by LLM).

        :param text:             Raw uplink text, e.g. "WILCO [MRN/3]"
        :param responds_to_mrn: If known, the downlink MRN this answers.
        :returns:                The stored CpdlcMessage.
        """
        text_clean = text.strip().upper()

        # Auto-detect responded MRN from "[MRN/N]" tag
        if responds_to_mrn is None:
            import re
            m = re.search(r'\[MRN/(\d+)\]', text_clean)
            if m:
                responds_to_mrn = int(m.group(1))

        with self._lock:
            mrn = self._next_mrn()
            status = self._classify_uplink(text_clean)
            msg = CpdlcMessage(
                mrn=mrn,
                direction=MessageDirection.UPLINK,
                text=text_clean,
                status=status,
                response_mrn=responds_to_mrn,
            )
            self._messages[mrn] = msg

            # Close the corresponding downlink
            if responds_to_mrn and responds_to_mrn in self._messages:
                self._messages[responds_to_mrn].status = status
                if responds_to_mrn in self._pending_mrns:
                    self._pending_mrns.remove(responds_to_mrn)

        event_bus.emit('cpdlc_uplink', msg.to_dict())
        return msg

    def _handle_uplink_raw(self, text: str):
        """EventBus handler: called when the LLM emits a CPDLC-formatted response."""
        self.receive_uplink(text)

    @staticmethod
    def _classify_uplink(text: str) -> CpdlcStatus:
        """Infer a CpdlcStatus from the first word(s) of an uplink."""
        if text.startswith("WILCO"):   return CpdlcStatus.WILCO
        if text.startswith("ROGER"):   return CpdlcStatus.ROGER
        if text.startswith("UNABLE"):  return CpdlcStatus.UNABLE
        if text.startswith("STANDBY"): return CpdlcStatus.STANDBY
        if text.startswith("LOGON ACCEPTED"): return CpdlcStatus.ROGER
        return CpdlcStatus.ROGER   # default — informational uplink

    # ── Pilot acknowledgement ─────────────────────────────────────────────────

    def pilot_respond(self, uplink_mrn: int, response: str) -> Optional[int]:
        """
        Pilot acknowledges an uplink with WILCO / UNABLE / ROGER / STANDBY.
        Returns the new downlink MRN, or None if uplink not found.
        """
        response = response.strip().upper()
        if response not in ("WILCO", "UNABLE", "ROGER", "STANDBY"):
            return None
        with self._lock:
            if uplink_mrn not in self._messages:
                return None
            self._messages[uplink_mrn].status = CpdlcStatus.CLOSED
        return self._create_downlink(response)

    # ── Query helpers ─────────────────────────────────────────────────────────

    def get_history(self, limit: int = 20) -> list[dict]:
        """Return the most recent messages as dicts, newest last."""
        with self._lock:
            msgs = sorted(self._messages.values(), key=lambda m: m.timestamp)
        return [m.to_dict() for m in msgs[-limit:]]

    def get_pending(self) -> list[dict]:
        """Return downlink messages awaiting an ATC response."""
        with self._lock:
            return [self._messages[mrn].to_dict()
                    for mrn in self._pending_mrns
                    if mrn in self._messages]

    # ── LLM prompt snippet ────────────────────────────────────────────────────

    def build_prompt_block(self) -> str:
        """
        Build a CPDLC context block for injection into the LLM system prompt.
        Included when the aircraft is in a CPDLC-capable context (Center / Oceanic).
        """
        lines = ["=== CPDLC SESSION ==="]

        with self._lock:
            active  = self.session_active
            station = self.logged_on_to or "N/A"
            pending = [self._messages[mrn].text
                       for mrn in self._pending_mrns
                       if mrn in self._messages]

        if active:
            lines.append(f"CPDLC LOGGED ON TO: {station}")
        else:
            lines.append("CPDLC: NOT LOGGED ON")

        if pending:
            lines.append("PENDING PILOT REQUESTS:")
            for p in pending[-5:]:   # last 5 pending
                lines.append(f"  - {p}")

        lines += [
            "",
            "CPDLC RESPONSE RULES:",
            "1. Respond using standard CPDLC uplink elements (WILCO, UNABLE, ROGER, STANDBY).",
            "2. Append [MRN/N] tag matching the downlink MRN when responding to a request.",
            "3. For altitude clearances use: 'CLIMB TO FL350 [MRN/N]' or 'DESCEND TO M840 [MRN/N]'.",
            "4. You may issue instructions via CPDLC when voice is not expected (oceanic/high-altitude).",
            "5. Mark the start of a CPDLC message with the prefix 'CPDLC:' so it can be parsed.",
            "=====================",
        ]
        return "\n".join(lines)


# ── Singleton ─────────────────────────────────────────────────────────────────
cpdlc_manager = CpdlcManager()
