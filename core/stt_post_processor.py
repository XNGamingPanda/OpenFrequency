"""
stt_post_processor.py — Aviation STT Post-Processor

Corrects common Whisper misrecognitions for:
  1. Airline telephony callsigns (全球航司 ICAO 呼号)
  2. ATC / aviation terminology (English + Chinese)
  3. NATO phonetic alphabet & phonetic numbers
  4. Chinese phonetic numbers (洞幺两三四五六拐八九)
  5. Runway / squawk / altitude patterns

Applied immediately after STT transcription, before the text reaches the LLM.
"""

from __future__ import annotations
import re

# ─────────────────────────────────────────────────────────────────────────────
# § 1  Airline telephony designators
#      key   = telephony word(s) that Whisper might produce (lowercase)
#      value = canonical written form (for display / LLM context)
# ─────────────────────────────────────────────────────────────────────────────

# Full telephony word lists drawn from ICAO Doc 8585
AIRLINE_TELEPHONY: dict[str, str] = {
    # ── China & Taiwan ────────────────────────────────────────────────────
    "air china":            "Air China",
    "airchina":             "Air China",
    "china air":            "Air China",       # common mishearing
    "chinese air":          "Air China",
    "国航":                  "Air China",
    "中国国际航空":           "Air China",

    "china eastern":        "China Eastern",
    "eastern":              "China Eastern",   # context-biased
    "china east":           "China Eastern",
    "东方":                  "China Eastern",
    "东航":                  "China Eastern",
    "中国东方":              "China Eastern",

    "china southern":       "China Southern",
    "southern":             "China Southern",
    "china south":          "China Southern",
    "南方":                  "China Southern",
    "南航":                  "China Southern",

    "xiamen air":           "Xiamen Air",
    "amoy":                 "Xiamen Air",
    "厦门航空":               "Xiamen Air",

    "shenzhen airlines":    "Shenzhen Air",
    "shenzhen air":         "Shenzhen Air",
    "深航":                  "Shenzhen Air",
    "深圳航空":               "Shenzhen Air",

    "sichuan airlines":     "Sichuan Air",
    "sichuan air":          "Sichuan Air",
    "川航":                  "Sichuan Air",
    "四川航空":               "Sichuan Air",

    "hainan airlines":      "Hainan Air",
    "hainan air":           "Hainan Air",
    "海南航空":               "Hainan Air",
    "海航":                  "Hainan Air",

    "chongqing airlines":   "Chongqing Air",
    "重庆航空":               "Chongqing Air",

    "loong air":            "Loong Air",
    "lucky air":            "Lucky Air",
    "西部航空":               "West Air",
    "west air":             "West Air",
    "九元航空":               "June Yao Air",
    "juneyao air":          "Juneyao Air",
    "吉祥航空":               "Juneyao Air",
    "春秋航空":               "Spring Airlines",
    "spring airlines":      "Spring Airlines",
    "春秋":                  "Spring Airlines",
    "上海航空":               "Shanghai Air",
    "shanghai air":         "Shanghai Air",
    "山东航空":               "Shandong Airlines",
    "shandong":             "Shandong Airlines",
    "厦航":                  "Xiamen Air",
    "深圳":                  "Shenzhen Air",
    "成都航空":               "Chengdu Airlines",

    "cathay pacific":       "Cathay Pacific",
    "cathay":               "Cathay Pacific",
    "国泰":                  "Cathay Pacific",
    "国泰航空":               "Cathay Pacific",

    "hong kong airlines":   "Hong Kong Airlines",
    "hong kong express":    "HK Express",
    "hk express":           "HK Express",

    "eva air":              "EVA Air",
    "eva":                  "EVA Air",
    "长荣航空":               "EVA Air",
    "china airlines":       "China Airlines",
    "中华航空":               "China Airlines",
    "mandarin airlines":    "Mandarin Airlines",
    "far eastern air":      "Far Eastern Air",
    "uni air":              "Uni Air",
    "transasia":            "TransAsia",
    "tigerair taiwan":      "Tigerair Taiwan",

    "air macau":            "Air Macau",
    "澳门航空":               "Air Macau",

    # ── Korea ─────────────────────────────────────────────────────────────
    "korean air":           "Korean Air",
    "koreanair":            "Korean Air",
    "asiana":               "Asiana",
    "asiana airlines":      "Asiana",
    "air busan":            "Air Busan",
    "jeju air":             "Jeju Air",
    "jin air":              "Jin Air",

    # ── Japan ─────────────────────────────────────────────────────────────
    "japan air":            "Japan Air",
    "japan airlines":       "Japan Air",
    "jal":                  "Japan Air",
    "all nippon":           "All Nippon",
    "ana":                  "All Nippon",
    "vanilla air":          "Vanilla Air",
    "peach":                "Peach Aviation",
    "jetstar japan":        "Jetstar Japan",
    "skymark":              "Skymark",
    "air do":               "Air Do",
    "solaseed":             "Solaseed Air",
    "fuji dream":           "Fuji Dream",
    "starflyer":            "Starflyer",

    # ── Southeast Asia ────────────────────────────────────────────────────
    "singapore airlines":   "Singapore Airlines",
    "singapore air":        "Singapore Airlines",
    "singapore":            "Singapore Airlines",
    "sia":                  "Singapore Airlines",
    "scoot":                "Scoot",
    "silkair":              "SilkAir",
    "thai airways":         "Thai Airways",
    "thai":                 "Thai Airways",
    "thai international":   "Thai Airways",
    "thai lion":            "Thai Lion Air",
    "nok air":              "Nok Air",
    "bangkok air":          "Bangkok Air",
    "vietnam airlines":     "Vietnam Airlines",
    "vietjet":              "VietJet Air",
    "bamboo airways":       "Bamboo Airways",
    "garuda":               "Garuda",
    "garuda indonesia":     "Garuda",
    "lion air":             "Lion Air",
    "batik air":            "Batik Air",
    "citilink":             "Citilink",
    "airasia":              "AirAsia",
    "air asia":             "AirAsia",
    "malindo":              "Malindo Air",
    "malaysia airlines":    "Malaysia Airlines",
    "malaysia":             "Malaysia Airlines",
    "philippines airlines": "Philippine Airlines",
    "philippine air":       "Philippine Airlines",
    "cebu pacific":         "Cebu Pacific",

    # ── Middle East ───────────────────────────────────────────────────────
    "emirates":             "Emirates",
    "etihad":               "Etihad",
    "qatar airways":        "Qatar Airways",
    "qatar":                "Qatar Airways",
    "gulf air":             "Gulf Air",
    "air arabia":           "Air Arabia",
    "flydubai":             "Flydubai",
    "oman air":             "Oman Air",
    "saudia":               "Saudia",
    "saudi arabian":        "Saudia",
    "kuwait airways":       "Kuwait Airways",

    # ── South Asia ────────────────────────────────────────────────────────
    "air india":            "Air India",
    "indigo":               "IndiGo",
    "indigo airlines":      "IndiGo",
    "spicejet":             "SpiceJet",
    "vistara":              "Vistara",
    "goair":                "Go Air",
    "pakistan international": "PIA",
    "pia":                  "PIA",
    "sri lankan":           "SriLankan Airlines",
    "srilankan":            "SriLankan Airlines",

    # ── Europe ────────────────────────────────────────────────────────────
    "lufthansa":            "Lufthansa",
    "british airways":      "British Airways",
    "speedbird":            "British Airways",
    "air france":           "Air France",
    "klm":                  "KLM",
    "royal dutch":          "KLM",
    "swiss":                "Swiss",
    "swiss international":  "Swiss",
    "austrian":             "Austrian",
    "iberia":               "Iberia",
    "finnair":              "Finnair",
    "scandinavian":         "Scandinavian",
    "sas":                  "Scandinavian",
    "turkish airlines":     "Turkish Airlines",
    "turkish":              "Turkish Airlines",
    "aeroflot":             "Aeroflot",
    "ryan air":             "Ryanair",
    "ryanair":              "Ryanair",
    "easyjet":              "easyJet",
    "easy jet":             "easyJet",
    "wizz air":             "Wizz Air",
    "vueling":              "Vueling",
    "tap air":              "TAP Air Portugal",
    "lot polish":           "LOT Polish",

    # ── Americas ──────────────────────────────────────────────────────────
    "united":               "United",
    "united airlines":      "United",
    "delta":                "Delta",
    "delta airlines":       "Delta",
    "american":             "American",
    "american airlines":    "American",
    "southwest":            "Southwest",
    "alaska":               "Alaska",
    "alaska airlines":      "Alaska",
    "jetblue":              "JetBlue",
    "jet blue":             "JetBlue",
    "spirit":               "Spirit",
    "frontier":             "Frontier",
    "air canada":           "Air Canada",
    "westjet":              "WestJet",
    "aeromexico":           "Aeromexico",
    "latam":                "LATAM",
    "tam":                  "LATAM",
    "gol":                  "GOL",
    "avianca":              "Avianca",

    # ── Africa & Pacific ──────────────────────────────────────────────────
    "qantas":               "Qantas",
    "jetstar":              "Jetstar",
    "virgin australia":     "Virgin Australia",
    "air new zealand":      "Air New Zealand",
    "air nz":               "Air New Zealand",
    "ethiopian":            "Ethiopian Airlines",
    "kenya airways":        "Kenya Airways",
    "south african":        "South African Airways",
    "egypt air":            "EgyptAir",
    "egyptair":             "EgyptAir",
    "royal air maroc":      "Royal Air Maroc",
}

# ─────────────────────────────────────────────────────────────────────────────
# § 2  Aviation terminology corrections (Whisper mishearing → correct form)
# ─────────────────────────────────────────────────────────────────────────────

# Case-insensitive substring replacements applied in order
AVIATION_CORRECTIONS: list[tuple[str, str]] = [
    # Clearance / ATC keywords
    (r'\bcleard\b',         'cleared'),
    (r'\bcleared for\b',    'cleared for'),
    (r'\bdecsend\b',        'descend'),
    (r'\bdescent\b',        'descend'),          # sometimes used wrong
    (r'\bascend\b',         'climb'),
    (r'\bkts\b',            'knots'),
    (r'\bkn\b',             'knots'),
    (r'\bflt\b',            'flight'),
    (r'\bapch\b',           'approach'),
    (r'\bdep\b',            'departure'),
    (r'\bsid\b',            'SID'),
    (r'\bstar\b(?!\s+\w)',  'STAR'),
    (r'\bills\b',           'ILS'),
    (r'\bvhf\b',            'VHF'),
    (r'\buhf\b',            'UHF'),
    (r'\batc\b',            'ATC'),
    (r'\batcas\b',          'TCAS'),
    (r'\bt\.?c\.?a\.?s\b',  'TCAS'),
    (r'\bq\.?n\.?h\b',      'QNH'),
    (r'\bq\.?f\.?e\b',      'QFE'),
    (r'\bq\.?n\.?e\b',      'QNE'),
    (r'\bvfr\b',            'VFR'),
    (r'\bifr\b',            'IFR'),
    (r'\bvor\b',            'VOR'),
    (r'\bdme\b',            'DME'),
    (r'\bndb\b',            'NDB'),
    (r'\brnav\b',           'RNAV'),
    (r'\brnp\b',            'RNP'),
    (r'\bfms\b',            'FMS'),
    (r'\befis\b',           'EFIS'),
    (r'\becam\b',           'ECAM'),
    (r'\beicam\b',          'ECAM'),
    (r'\bmayday mayday\b',  'MAYDAY MAYDAY'),
    (r'\bpan pan\b',        'PAN PAN'),
    (r'\bwilco\b',          'wilco'),
    (r'\baffirm\b',         'affirm'),
    (r'\bnegative\b',       'negative'),
    (r'\bunable\b',         'unable'),
    (r'\broger\b',          'roger'),

    # Frequency misreadings
    (r'\b(\d{3})\.(\d{1,3})\b',  lambda m: m.group(0)),  # preserve

    # "niner" → "9" for numbers in context
    (r'\bniner\b',  '9'),
    (r'\bzeero\b',  '0'),

    # Common mishearings of aviation words
    (r'\bground control\b',  'ground'),
    (r'\btower control\b',   'tower'),
    (r'\bapproach control\b','approach'),
    (r'\bdeparture control\b','departure'),

    # Squawk / transponder
    (r'\bsquak\b',      'squawk'),
    (r'\bsquak\b',      'squawk'),
    (r'\btransponder\b','transponder'),

    # Holding patterns
    (r'\bholding pattern\b',    'holding'),
    (r'\bhold short\b',         'hold short'),

    # Runway designators - normalize "runway two seven left" style
    # (handled separately in callsign processing)
]

# ─────────────────────────────────────────────────────────────────────────────
# § 3  NATO phonetic alphabet
# ─────────────────────────────────────────────────────────────────────────────
NATO = {
    'alpha': 'A', 'alfa': 'A',
    'bravo': 'B',
    'charlie': 'C',
    'delta': 'D',
    'echo': 'E',
    'foxtrot': 'F',
    'golf': 'G',
    'hotel': 'H',
    'india': 'I',
    'juliet': 'J', 'juliett': 'J',
    'kilo': 'K',
    'lima': 'L',
    'mike': 'M',
    'november': 'N',
    'oscar': 'O',
    'papa': 'P',
    'quebec': 'Q',
    'romeo': 'R',
    'sierra': 'S',
    'tango': 'T',
    'uniform': 'U',
    'victor': 'V',
    'whiskey': 'W',
    'x-ray': 'X', 'xray': 'X',
    'yankee': 'Y',
    'zulu': 'Z',
    # digits
    'zero': '0',
    'one': '1',
    'two': '2',
    'three': '3',
    'four': '4',
    'five': '5',
    'six': '6',
    'seven': '7',
    'eight': '8',
    'niner': '9', 'nine': '9',
}

# ─────────────────────────────────────────────────────────────────────────────
# § 4  Chinese phonetic digit mapping (CAAC standard)
# ─────────────────────────────────────────────────────────────────────────────
ZH_DIGITS = {
    '洞': '0', '幺': '1', '两': '2', '三': '3', '四': '4',
    '五': '5', '六': '6', '拐': '7', '八': '8', '九': '9',
}

# ─────────────────────────────────────────────────────────────────────────────
# § 5  Build sorted airline pattern list for regex (longest match first)
# ─────────────────────────────────────────────────────────────────────────────
def _build_airline_pattern() -> re.Pattern:
    keys = sorted(AIRLINE_TELEPHONY.keys(), key=len, reverse=True)
    escaped = [re.escape(k) for k in keys]
    return re.compile(r'\b(?:' + '|'.join(escaped) + r')\b', re.IGNORECASE)

_AIRLINE_RE = _build_airline_pattern()

# Build NATO pattern
_NATO_WORDS = sorted(NATO.keys(), key=len, reverse=True)
_NATO_RE = re.compile(
    r'\b(' + '|'.join(re.escape(w) for w in _NATO_WORDS) + r')\b',
    re.IGNORECASE,
)

# Chinese phonetic number pattern
_ZH_DIGIT_RE = re.compile('[' + ''.join(ZH_DIGITS.keys()) + ']')

# Callsign detection: airline + 1-4 digits optionally followed by letter
_CALLSIGN_RE = re.compile(
    r'\b([A-Z]{2,3})\s*(\d{1,4})\s*([A-Z]?)\b'
)

# ─────────────────────────────────────────────────────────────────────────────
# § 6  Core correction function
# ─────────────────────────────────────────────────────────────────────────────

def correct_aviation_text(text: str) -> str:
    """
    Main entry point: apply all correction passes to raw STT text.
    Returns corrected text ready for the LLM / quick-reply engine.
    """
    if not text:
        return text

    # 1. Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    # 2. Airline telephony normalization
    def _fix_airline(m: re.Match) -> str:
        return AIRLINE_TELEPHONY.get(m.group(0).lower(), m.group(0))
    text = _AIRLINE_RE.sub(_fix_airline, text)

    # 3. Aviation term corrections
    for pattern, replacement in AVIATION_CORRECTIONS:
        if callable(replacement):
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        else:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # 4. Chinese phonetic digits → Arabic digits (for callsign extraction)
    #    e.g. "国航幺两三四" → "国航1234"
    text = _ZH_DIGIT_RE.sub(lambda m: ZH_DIGITS[m.group(0)], text)

    # 5. NATO phonetic alphabet collapse in callsign context
    #    e.g. "Bravo Alpha seven niner" → "BA79"
    text = _collapse_nato_callsign(text)

    return text


def _collapse_nato_callsign(text: str) -> str:
    """
    Collapse sequences of NATO words + digits that look like a callsign.
    e.g. "November Golf zero one two" → "NG012"
    e.g. "Alpha Bravo seven" → "AB7"
    Only collapses when 2+ consecutive NATO/digit tokens appear.
    """
    tokens = text.split()
    result = []
    buffer = []   # accumulate possible callsign tokens
    i = 0

    while i < len(tokens):
        word = tokens[i].rstrip(',.:;')
        lower = word.lower()
        if lower in NATO:
            buffer.append(NATO[lower])
        elif word.isdigit() and len(word) == 1:
            buffer.append(word)
        else:
            if len(buffer) >= 2:
                result.append(''.join(buffer))
            elif buffer:
                result.extend(buffer)
            buffer = []
            result.append(tokens[i])
        i += 1

    if len(buffer) >= 2:
        result.append(''.join(buffer))
    elif buffer:
        result.extend(buffer)

    return ' '.join(result)


def build_whisper_hotwords(config: dict) -> str:
    """
    Build a hotwords/initial-prompt string for Whisper if the backend supports it.
    Returns a comma-separated phrase list suitable for the `hotwords` parameter.
    """
    # Get user's airline / callsign from config
    callsign = config.get('user_profile', {}).get('callsign', '')
    airline_icao = config.get('user_profile', {}).get('airline_icao', '')

    phrases = []

    # Add current callsign first (highest priority)
    if callsign:
        phrases.append(callsign)

    # Core aviation vocabulary (English)
    phrases += [
        # clearances
        "cleared for takeoff", "cleared to land", "cleared for the ILS",
        "cleared for approach", "line up and wait", "go around",
        "hold short", "continue taxi", "cross runway",
        # altitude
        "climb and maintain", "descend and maintain", "maintain flight level",
        "flight level", "altitude", "transition level",
        # navigation
        "direct to", "fly heading", "turn left heading", "turn right heading",
        "resume own navigation", "own navigation",
        # speed
        "reduce speed", "speed", "knots",
        # frequency
        "contact", "frequency", "MHz",
        # squawk
        "squawk", "ident", "transponder",
        # weather
        "QNH", "QFE", "altimeter", "wind", "visibility",
        # acknowledgement
        "wilco", "roger", "affirm", "negative", "unable",
        "say again", "stand by", "radar contact",
        # emergency
        "MAYDAY", "PAN PAN",
        # phonetic alphabet
        "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot",
        "Golf", "Hotel", "India", "Juliet", "Kilo", "Lima",
        "Mike", "November", "Oscar", "Papa", "Quebec", "Romeo",
        "Sierra", "Tango", "Uniform", "Victor", "Whiskey",
        "X-ray", "Yankee", "Zulu",
        "niner",
    ]

    # Add top airline telephony designators (canonical forms)
    top_airlines = [
        "Air China", "China Eastern", "China Southern", "Xiamen Air",
        "Sichuan Air", "Hainan Air", "Shenzhen Air", "Cathay Pacific",
        "Korean Air", "Asiana", "Japan Air", "All Nippon",
        "Singapore Airlines", "Thai Airways", "Malaysia Airlines",
        "Emirates", "Etihad", "Qatar Airways",
        "Lufthansa", "British Airways", "Air France", "KLM",
        "United", "Delta", "American",
        "Qantas", "Air New Zealand",
    ]
    phrases += top_airlines

    # Chinese ATC terms
    phrases += [
        "复诵正确", "收到", "明白", "否定", "稍等", "请重复",
        "可以起飞", "可以落地", "复飞", "进跑道等待",
        "爬升", "下降", "保持高度", "飞航向",
        "联系", "更换频率", "气压拨正", "应答机",
        "雷达接触", "跑道", "滑行", "等待",
    ]

    return ', '.join(phrases)
