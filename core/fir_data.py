"""
FIR (Flight Information Region) boundary detection using ray-casting.
No external dependencies — pure Python, PyInstaller-friendly.

Polygon coordinates: [longitude, latitude] pairs.
Boundaries are simplified but accurate enough for enroute FIR handoff detection.

External GeoJSON loading (optional):
  Place a VAT-Spy-compatible FIRs.geojson at data/firs.geojson.
  On startup, FIRDetector will merge those polygons into the built-in set.
  Built-in entries act as offline fallback when the file is absent.
"""
import json
import os

FIR_BOUNDARIES = {
    # ── China ─────────────────────────────────────────────────────────────────
    "ZSHA": {
        "name": "Shanghai FIR",
        "name_zh": "上海飞行情报区",
        "center_freq": 122.050,
        "language": "zh",
        "polygon": [
            [117.0, 27.0], [117.0, 35.0], [122.0, 35.0],
            [122.0, 33.0], [125.0, 33.0], [125.0, 27.0],
        ],
    },
    "ZBPE": {
        "name": "Beijing FIR",
        "name_zh": "北京飞行情报区",
        "center_freq": 128.825,
        "language": "zh",
        "polygon": [
            [107.0, 35.0], [107.0, 54.0], [135.5, 54.0],
            [135.5, 35.0], [125.0, 35.0], [122.0, 35.0],
            [117.0, 35.0],
        ],
    },
    "ZGZU": {
        "name": "Guangzhou FIR",
        "name_zh": "广州飞行情报区",
        "center_freq": 128.000,
        "language": "zh",
        "polygon": [
            [107.0, 18.0], [107.0, 27.0], [117.0, 27.0],
            [125.0, 27.0], [125.0, 18.0],
        ],
    },
    "ZWUQ": {
        "name": "Urumqi FIR",
        "name_zh": "乌鲁木齐飞行情报区",
        "center_freq": 128.600,
        "language": "zh",
        "polygon": [
            [73.0, 35.0], [73.0, 54.0], [107.0, 54.0],
            [107.0, 35.0], [107.0, 27.0], [73.0, 27.0],
        ],
    },
    # ── East Asia ──────────────────────────────────────────────────────────────
    # ROZE checked before RJJJ — polygons overlap at [120-131E, 24-27N]
    "ROZE": {
        "name": "Naha FIR",
        "name_zh": "那霸飞行情报区",
        "center_freq": 128.200,
        "language": "en",
        # Covers Ryukyu/Okinawa chain east to 131E, north to 27N
        "polygon": [
            [120.0, 18.0], [120.0, 27.0], [131.0, 27.0],
            [131.0, 18.0],
        ],
    },
    # RKRR checked before RJJJ — L-shaped polygon distinguishes Jeju from Fukuoka:
    #   West of 129E it extends south to 32N (covers Jeju at 33.5N, 126.5E)
    #   East of 129E it only extends to 34.5N (Korea Strait / Tsushima boundary)
    "RKRR": {
        "name": "Incheon FIR",
        "name_zh": "仁川飞行情报区",
        "center_freq": 128.700,
        "language": "en",
        "polygon": [
            [122.0, 32.0], [122.0, 44.0], [135.0, 44.0],
            [135.0, 34.5], [129.0, 34.5], [129.0, 32.0],
        ],
    },
    "RJJJ": {
        "name": "Fukuoka FIR",
        "name_zh": "福冈飞行情报区",
        "center_freq": 133.600,
        "language": "en",
        # L-shaped east of Korea Strait: west side up to 33N, east side up to 34.5N
        "polygon": [
            [122.0, 24.0], [122.0, 33.0], [129.0, 33.0],
            [129.0, 34.5], [136.0, 34.5], [136.0, 24.0],
        ],
    },
    "RJTT": {
        "name": "Tokyo FIR",
        "name_zh": "东京飞行情报区",
        "center_freq": 133.200,
        "language": "en",
        "polygon": [
            [136.0, 24.0], [136.0, 46.0], [150.0, 46.0],
            [150.0, 24.0],
        ],
    },
    "VHHK": {
        "name": "Hong Kong FIR",
        "name_zh": "香港飞行情报区",
        "center_freq": 128.200,
        "language": "en",
        "polygon": [
            [107.0, 18.0], [107.0, 27.0], [125.0, 27.0],
            [125.0, 18.0], [120.0, 18.0],
        ],
    },
    "RCAA": {
        "name": "Taipei FIR",
        "name_zh": "台北飞行情报区",
        "center_freq": 127.900,
        "language": "en",
        "polygon": [
            [116.0, 20.0], [116.0, 27.0], [120.0, 27.0],
            [125.0, 27.0], [125.0, 18.0], [120.0, 18.0],
        ],
    },
}


def _ray_cast(lon: float, lat: float, polygon: list) -> bool:
    """
    Ray-casting algorithm: cast a ray rightward from (lon, lat) and count
    how many times it crosses polygon edges. Odd = inside, even = outside.
    """
    x, y = lon, lat
    n = len(polygon)
    inside = False
    p1x, p1y = polygon[0]
    for i in range(1, n + 1):
        p2x, p2y = polygon[i % n]
        if min(p1y, p2y) < y <= max(p1y, p2y):
            if x <= max(p1x, p2x):
                if p1y != p2y:
                    xints = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                if p1x == p2x or x <= xints:
                    inside = not inside
        p1x, p1y = p2x, p2y
    return inside


_GEOJSON_SEARCH_PATHS = [
    os.path.join(os.path.dirname(__file__), '..', 'data', 'firs.geojson'),
    os.path.join(os.path.dirname(__file__), '..', 'assets', 'firs.geojson'),
]


def _load_geojson(path: str) -> dict:
    """
    Load VAT-Spy / OpenAIP style FIRs.geojson and convert to our internal format.
    Each Feature must have properties.id (ICAO) and geometry.coordinates.
    """
    result = {}
    try:
        with open(path, encoding='utf-8') as f:
            gj = json.load(f)
        for feat in gj.get('features', []):
            props = feat.get('properties', {})
            fir_id = props.get('id') or props.get('icao') or props.get('ICAO')
            if not fir_id:
                continue
            geom = feat.get('geometry', {})
            gtype = geom.get('type', '')
            coords = geom.get('coordinates', [])
            # Use only the outer ring of Polygon / first Polygon of MultiPolygon
            if gtype == 'Polygon' and coords:
                poly = coords[0]
            elif gtype == 'MultiPolygon' and coords:
                poly = coords[0][0]
            else:
                continue
            # GeoJSON coords are [lon, lat] — same as our format
            result[fir_id] = {
                'name': props.get('name', fir_id),
                'name_zh': props.get('name_zh', ''),
                'center_freq': float(props.get('center_freq', 0) or 0),
                'language': props.get('language', 'en'),
                'fir_type': props.get('fir_type', 'RADAR'),
                'polygon': poly,
                '_source': 'geojson',
            }
        print(f"FIRDetector: Loaded {len(result)} FIRs from {path}")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"FIRDetector: Failed to load GeoJSON {path}: {e}")
    return result


class FIRDetector:
    def __init__(self):
        # Start with built-in fallback data
        self.boundaries: dict = {k: dict(v) for k, v in FIR_BOUNDARIES.items()}
        # Try to enrich with external GeoJSON (built-in entries take priority for
        # FIRs we have hand-tuned; external data fills in the rest of the world)
        for path in _GEOJSON_SEARCH_PATHS:
            ext = _load_geojson(path)
            if ext:
                for code, info in ext.items():
                    if code not in self.boundaries:
                        self.boundaries[code] = info
                break  # stop at first successful file

    def get_current_fir(self, lat: float, lon: float) -> str | None:
        """Return the FIR code containing (lat, lon), or None if unknown."""
        for code, data in self.boundaries.items():
            if _ray_cast(lon, lat, data["polygon"]):
                return code
        return None

    def get_fir_info(self, fir_code: str) -> dict | None:
        return self.boundaries.get(fir_code)

    def get_center_freq(self, fir_code: str) -> float | None:
        info = self.get_fir_info(fir_code)
        return info["center_freq"] if info else None

    def get_language(self, fir_code: str) -> str:
        info = self.get_fir_info(fir_code)
        return info["language"] if info else "en"


# ── Singleton ──────────────────────────────────────────────────────────────────
fir_detector = FIRDetector()


# ── Self-test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [
        # (description, lat, lon, expected_fir)
        ("上海浦东 ZSPD",      31.14, 121.80, "ZSHA"),
        ("济州国际 RKPC",      33.51, 126.49, "RKRR"),
        ("九州福冈 RJFF",      33.58, 130.45, "RJJJ"),
        ("北京首都 ZBAA",      40.08, 116.58, "ZBPE"),
        ("广州白云 ZGGG",      23.39, 113.30, "ZGZU"),
        ("那霸 ROAH",          26.20, 127.65, "ROZE"),
        ("东京成田 RJAA",      35.77, 140.39, "RJTT"),
        ("公海（中日之间）",   30.00, 126.00, "RJJJ"),  # East China Sea oceanic sector → Fukuoka FIR
    ]

    detector = FIRDetector()
    all_pass = True
    print(f"{'测试点':<20} {'坐标':^22} {'预期':^6} {'结果':^6} {'状态'}")
    print("-" * 65)
    for desc, lat, lon, expected in tests:
        result = detector.get_current_fir(lat, lon)
        ok = result == expected
        if not ok:
            all_pass = False
        status = "✓" if ok else f"✗ (got {result})"
        print(f"{desc:<20} ({lat:>6.2f}, {lon:>7.2f})  {str(expected):^6}  {str(result):^6}  {status}")

    print("-" * 65)
    print("全部通过 ✓" if all_pass else "⚠ 有测试未通过，需要调整多边形边界")
