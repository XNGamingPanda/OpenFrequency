"""
InstructionExtractor - extracts pilot-actionable ATC instructions into cards.
"""
import re


class InstructionExtractor:
    TAXI_STOP_WORDS = {
        "TO", "VIA", "RUNWAY", "HOLD", "SHORT", "OF", "CONTACT", "ON",
        "TAXI", "CLEARED", "CLIMB", "DESCEND", "MAINTAIN", "HEADING",
        "SPEED", "QNH", "ALTIMETER", "SQUAWK", "ILS", "VISUAL", "APPROACH",
    }

    @classmethod
    def extract(cls, text):
        text = (text or "").strip()
        if not text:
            return []

        cards = []
        lower = text.lower()

        cls._add_altitude(cards, lower)
        cls._add_heading(cards, lower)
        cls._add_speed(cards, lower)
        cls._add_qnh(cards, lower)
        cls._add_approach(cards, lower)
        cls._add_frequency(cards, lower)
        cls._add_squawk(cards, lower)
        cls._add_taxi(cards, text)

        deduped = []
        seen = set()
        for card in cards:
            key = (card["type"], card["value"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(card)
        return deduped

    @classmethod
    def _add_altitude(cls, cards, text):
        # China metric RVSM: "M840", "M8400", "8400M", "8400米", "climb to M890"
        metric = re.search(
            r"\b(?:m(\d{3,5})|(\d{3,5})[m米])\b",
            text,
        )
        if metric:
            raw = metric.group(1) or metric.group(2)
            val = int(raw)
            # CAAC abbreviated: M840 = 8400 m (3-digit code × 10 = metres)
            # Full form: M8400 or 8400M/8400米 = 8400 m directly
            if val < 1000:
                val_m = val * 10    # abbreviated: M840 → 8400 m
            else:
                val_m = val         # full form: 8400
            # Store as CAAC abbreviated string M840
            cards.append(cls._card("ALT", f"M{val_m // 10}", "Altitude (米)"))
            return

        fl = re.search(r"\b(?:fl|flight level)\s?(\d{2,3})\b", text)
        if fl:
            cards.append(cls._card("ALT", f"FL{fl.group(1)}", "Altitude"))
            return

        match = re.search(
            r"\b(?:climb(?: and maintain)?|descend(?: and maintain)?|maintain|altitude)\s+(?:to\s+)?(\d{3,5})\b",
            text,
        )
        if match:
            value = int(match.group(1))
            if value < 600:
                value *= 100
            cards.append(cls._card("ALT", str(value), "Altitude"))

    @classmethod
    def _add_heading(cls, cards, text):
        match = re.search(r"\b(?:fly heading|heading|turn (?:left|right) heading)\s+(\d{2,3})\b", text)
        if match:
            cards.append(cls._card("HDG", f"{int(match.group(1)) % 360:03d}", "Heading"))

    @classmethod
    def _add_speed(cls, cards, text):
        match = re.search(
            r"\b(?:maintain speed|speed|reduce speed to|increase speed to|keep speed)\s+(\d{2,3})\b",
            text,
        )
        if match:
            cards.append(cls._card("SPD", match.group(1), "Speed"))

    @classmethod
    def _add_qnh(cls, cards, text):
        qnh = re.search(r"\bqnh\s+(\d{3,4}(?:\.\d+)?)\b", text)
        if qnh:
            cards.append(cls._card("QNH", qnh.group(1), "QNH"))
        altim = re.search(r"\baltimeter\s+(\d{2}\.\d{2})\b", text)
        if altim:
            cards.append(cls._card("ALTIM", altim.group(1), "Altimeter"))

    @classmethod
    def _add_approach(cls, cards, text):
        match = re.search(r"\b(cleared (?:ils|rnav|visual|vor|loc|ndb)[\w\s/-]*?approach(?: runway)?\s*([0-9]{1,2}[lrc]?))", text)
        if match:
            cards.append(cls._card("APP", cls._clean_approach(match.group(1)), "Approach"))
            return
        match = re.search(r"\b((?:ils|rnav|visual|vor|loc|ndb)\s+(?:runway\s+)?[0-9]{1,2}[lrc]?\s+approach)\b", text)
        if match:
            cards.append(cls._card("APP", cls._clean_approach(match.group(1)), "Approach"))

    @classmethod
    def _add_frequency(cls, cards, text):
        if not any(word in text for word in ("contact", "frequency", "monitor", "switch")):
            return
        match = re.search(r"\b(1[1-3][0-9]\.\d{2,3})\b", text)
        if match:
            cards.append(cls._card("FREQ", f"{float(match.group(1)):.3f}", "Frequency"))

    @classmethod
    def _add_squawk(cls, cards, text):
        match = re.search(r"\bsquawk\s+(\d{4})\b", text)
        if match:
            cards.append(cls._card("SQ", match.group(1), "Squawk"))

    @classmethod
    def _add_taxi(cls, cards, text):
        lower = text.lower()
        if "taxi" not in lower:
            return
        route_match = re.search(r"\bvia\s+(.+?)(?:\.|, hold short| hold short| runway| contact|$)", text, re.IGNORECASE)
        if not route_match:
            return

        route_text = route_match.group(1)
        tokens = []
        for token in re.split(r"[\s,]+", route_text):
            clean = token.strip().strip(".").upper()
            if not clean or clean in cls.TAXI_STOP_WORDS:
                continue
            if re.fullmatch(r"[A-Z][0-9A-Z]?", clean) or re.fullmatch(r"[0-9]{1,2}[LRC]?", clean):
                tokens.append(clean)
        if tokens:
            cards.append(cls._card("TAXI", " ".join(tokens[:12]), "Taxi Route"))

    @staticmethod
    def _clean_approach(value):
        value = re.sub(r"\bcleared\s+", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s+", " ", value).strip()
        return value.upper()

    @staticmethod
    def _card(card_type, value, label):
        return {
            "type": card_type,
            "value": value,
            "label": label,
        }
