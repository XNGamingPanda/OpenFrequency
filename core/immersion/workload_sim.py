import random


# Traffic count → effective busy level mapping
# 0 aircraft  → silent airport, skip standby entirely
# 1–3         → low
# 4–10        → medium
# 11+         → high
_TRAFFIC_BUSY_MAP = [
    (0,  0,  'silent'),
    (1,  3,  'low'),
    (4,  10, 'medium'),
    (11, 999, 'high'),
]

_STANDBY_THRESHOLDS = {'silent': 0.0, 'low': 0.05, 'medium': 0.10, 'high': 0.30}
_IGNORE_THRESHOLDS  = {'silent': 0.0, 'low': 0.00, 'medium': 0.05, 'high': 0.15}


class WorkloadSimulator:
    def __init__(self, config):
        imm = config.get('immersion', {})
        self.enabled        = imm.get('enable_standby_simulation', True)
        self.busy_level     = imm.get('busy_level', 'medium')
        self.auto_busy      = imm.get('auto_busy_level', True)   # derive from traffic count
        self._traffic_count = 0                                   # updated by LogicManager

    # ── Public API ───────────────────────────────────────────────────────────

    def update_traffic_count(self, count: int):
        """Call this whenever the nearby aircraft list changes."""
        self._traffic_count = max(0, int(count))

    def _effective_level(self) -> str:
        """Return the busy level to use, auto-derived from traffic if enabled."""
        if not self.auto_busy:
            return self.busy_level
        n = self._traffic_count
        for lo, hi, level in _TRAFFIC_BUSY_MAP:
            if lo <= n <= hi:
                return level
        return 'high'

    def should_standby(self) -> bool:
        if not self.enabled:
            return False
        level = self._effective_level()
        return random.random() < _STANDBY_THRESHOLDS.get(level, 0.10)

    def should_ignore(self) -> bool:
        """Determines if ATC completely misses the call (silence)."""
        if not self.enabled:
            return False
        level = self._effective_level()
        return random.random() < _IGNORE_THRESHOLDS.get(level, 0.0)

    @property
    def effective_busy_level(self) -> str:
        """Readable label shown in UI (e.g. 'medium (auto)')."""
        level = self._effective_level()
        if self.auto_busy:
            return f"{level} (auto · {self._traffic_count} aircraft)"
        return level