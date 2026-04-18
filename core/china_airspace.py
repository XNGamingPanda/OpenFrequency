"""
china_airspace.py — Metric RVSM altitude helpers for Chinese airspace.

China uses metric altitudes above transition altitude in controlled airspace.
CAAC metric RVSM levels follow the semicircular rule based on magnetic track:

  Eastbound (000–179°): M8400, M8900, M9400, M9900, M10400, M10900, M11400, M11900, M12500
  Westbound (180–359°): M8600, M9100, M9600, M10100, M10600, M11100, M11600, M12100

Altitude string format used in CAAC clearances: "M840" (= 8400m) or plain metres "8400M".

China FIRs covered: ZBPE (Beijing), ZGZU (Guangzhou), ZSHA (Shanghai), ZHWH (Wuhan),
                    ZUCK (Chengdu), ZPKM (Kunming), ZUUM (Urumqi), ZWUQ (Urumqi upper)
"""

from __future__ import annotations
import re
from typing import Optional

# ── Chinese FIR boundary (simplified bounding boxes) ────────────────────────
# A point is "in China metric airspace" if its longitude is 73–135°E and
# latitude is 18–54°N.  This covers mainland China incl. HKFIR & Macau.
_CHINA_LON_MIN = 73.0
_CHINA_LON_MAX = 135.5
_CHINA_LAT_MIN = 18.0
_CHINA_LAT_MAX = 54.0


def is_in_china_airspace(lat: float, lon: float) -> bool:
    """Return True if the coordinate falls within Chinese controlled airspace."""
    return (
        _CHINA_LAT_MIN <= lat <= _CHINA_LAT_MAX
        and _CHINA_LON_MIN <= lon <= _CHINA_LON_MAX
    )


# ── RVSM level tables (metres, RVSM band 8400–12500 m) ───────────────────────
# Semi-circular rule on magnetic track:
#   000–179  →  eastbound levels
#   180–359  →  westbound levels
_EASTBOUND_LEVELS_M = [8400, 8900, 9400, 9900, 10400, 10900, 11400, 11900, 12500]
_WESTBOUND_LEVELS_M = [8600, 9100, 9600, 10100, 10600, 11100, 11600, 12100]

# Below RVSM band (< 8400 m) use 300 m separation:
_LOWER_EASTBOUND_M = [900, 1200, 1500, 1800, 2100, 2400, 2700, 3000,
                      3600, 4200, 4800, 5400, 6000, 6600, 7200, 7800, 8100]
_LOWER_WESTBOUND_M = [600, 900, 1200, 1500, 1800, 2100, 2400, 2700, 3000,
                      3600, 4200, 4800, 5400, 6000, 6600, 7200, 7800, 8400]


def _rvsm_levels(track_deg: float) -> list[int]:
    """Return the applicable metric RVSM level list for a given magnetic track."""
    track_deg = track_deg % 360
    return _EASTBOUND_LEVELS_M if track_deg < 180 else _WESTBOUND_LEVELS_M


def nearest_metric_rvsm_level(altitude_m: float, track_deg: float) -> int:
    """
    Given an altitude in metres and a magnetic track, return the nearest
    valid Chinese RVSM level in metres.
    """
    levels = _rvsm_levels(track_deg)
    return min(levels, key=lambda lv: abs(lv - altitude_m))


# ── Unit conversion helpers ───────────────────────────────────────────────────
_FT_PER_METRE = 3.28084

def metres_to_feet(m: float) -> int:
    return int(round(m * _FT_PER_METRE / 100) * 100)

def feet_to_metres(ft: float) -> int:
    return int(round(ft / _FT_PER_METRE / 100) * 100)

def fl_to_metres(fl: int) -> int:
    """Convert a Flight Level (e.g. 350 → 35000 ft) to metres."""
    return feet_to_metres(fl * 100)


# ── Altitude string parsing ───────────────────────────────────────────────────
# Recognised formats:
#   "M840"   → 8400 m    (CAAC abbreviated: M + hundreds)
#   "M8400"  → 8400 m    (explicit metres)
#   "8400M"  → 8400 m
#   "8400米" → 8400 m
#   "FL350"  → 35000 ft  (non-metric; returned as-is flag)

_RE_M_SHORT  = re.compile(r'^[Mm](\d{3})$')      # M840 → 840 × 10 = 8400 m
_RE_M_FULL   = re.compile(r'^[Mm](\d{4,5})$')    # M8400 → 8400 m
_RE_M_SUFFIX = re.compile(r'^(\d{3,5})[Mm米]$')  # 8400M / 8400米
_RE_FL       = re.compile(r'^[Ff][Ll](\d{2,3})$')


class AltitudeValue:
    """Parsed altitude, either metric (metres) or imperial (feet/FL)."""
    __slots__ = ('metres', 'feet', 'is_metric', 'raw')

    def __init__(self, metres: Optional[int], feet: Optional[int],
                 is_metric: bool, raw: str):
        self.metres = metres
        self.feet = feet
        self.is_metric = is_metric
        self.raw = raw

    def to_caac_string(self) -> str:
        """Return CAAC abbreviated metric string, e.g. 'M840' (= 8400 m)."""
        if self.metres is not None:
            return f"M{self.metres // 10}"
        return self.raw

    def to_full_metres_string(self) -> str:
        """Return full metric string, e.g. '8400米'."""
        if self.metres is not None:
            return f"{self.metres}米"
        return self.raw

    def __repr__(self):
        return f"<AltitudeValue raw={self.raw!r} metres={self.metres} feet={self.feet}>"


def parse_altitude_string(s: str) -> Optional[AltitudeValue]:
    """
    Parse an altitude string into an AltitudeValue.
    Returns None if the string is not a recognised altitude format.
    """
    s = s.strip()

    m = _RE_M_SHORT.match(s)
    if m:
        metres = int(m.group(1)) * 10   # M840 = 8400 m (CAAC abbreviated: hundreds × 10)
        return AltitudeValue(metres, metres_to_feet(metres), True, s)

    m = _RE_M_FULL.match(s)
    if m:
        metres = int(m.group(1))
        return AltitudeValue(metres, metres_to_feet(metres), True, s)

    m = _RE_M_SUFFIX.match(s)
    if m:
        metres = int(m.group(1))
        return AltitudeValue(metres, metres_to_feet(metres), True, s)

    m = _RE_FL.match(s)
    if m:
        ft = int(m.group(1)) * 100
        return AltitudeValue(None, ft, False, s)

    return None


# ── LLM prompt snippet ────────────────────────────────────────────────────────

def build_china_rvsm_prompt_block(track_deg: Optional[float] = None) -> str:
    """
    Return a text block ready to be injected into the LLM system prompt when
    the aircraft is in Chinese airspace.

    If track_deg is provided, lists only the applicable semi-circular levels;
    otherwise lists both directions.
    """
    lines = [
        "=== CHINA METRIC RVSM AIRSPACE ===",
        "You are operating in Chinese airspace. CAAC mandates metric altitudes.",
        "Use METRES (米/M) for all altitude clearances, NOT flight levels or feet.",
        "Altitude format: 'M840' = 8400 metres (abbreviated), or '8400米'.",
        "",
        "RVSM separation levels (semi-circular rule on magnetic track):",
    ]

    if track_deg is not None:
        direction = "Eastbound (000–179°)" if (track_deg % 360) < 180 else "Westbound (180–359°)"
        levels = _rvsm_levels(track_deg)
        levels_str = ", ".join(f"M{lv//10}" for lv in levels)
        lines.append(f"  {direction}: {levels_str}")
    else:
        east_str = ", ".join(f"M{lv//10}" for lv in _EASTBOUND_LEVELS_M)
        west_str = ", ".join(f"M{lv//10}" for lv in _WESTBOUND_LEVELS_M)
        lines.append(f"  Eastbound (000–179°): {east_str}")
        lines.append(f"  Westbound (180–359°): {west_str}")

    lines += [
        "",
        "CRITICAL: Issue altitude clearances using Chinese metric format,",
        "e.g. 'Climb and maintain M890' or 'Descend and maintain M840'.",
        "Do NOT use FL or feet when in Chinese airspace above transition altitude (3000m/9843ft).",
        "===================================",
    ]
    return "\n".join(lines)
