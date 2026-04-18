import json
import re


class ATCIntentRouter:
    """Routes pilot speech through rules, light semantic parsing, and escalation."""

    SIMPLE_RULES = [
        (
            "request_taxi",
            re.compile(r"\b(request|ready for)\s+(taxi|taxiing)\b", re.I),
            "Pilot requests taxi instructions.",
        ),
        (
            "request_pushback",
            re.compile(r"\b(request|ready for)\s+(pushback|push back)\b", re.I),
            "Pilot requests pushback clearance.",
        ),
        (
            "request_ifr_clearance",
            re.compile(r"\b(request|ready for)\s+(ifr )?(clearance|clearance delivery)\b", re.I),
            "Pilot requests IFR clearance.",
        ),
        (
            "ready_departure",
            re.compile(r"\bready\b.*\b(departure|takeoff)\b|\bholding short\b", re.I),
            "Pilot is ready for departure.",
        ),
        (
            "with_atis",
            re.compile(r"\bwith information\b|\bwith atis\b", re.I),
            "Pilot checks in with ATIS information received.",
        ),
        (
            "say_again",
            re.compile(r"\b(say again|repeat|radio check|unreadable)\b", re.I),
            "Pilot requests the last transmission again.",
        ),
        (
            "request_descent",
            re.compile(r"\brequest\b.*\bdescent\b|\brequest lower\b", re.I),
            "Pilot requests descent.",
        ),
        (
            "request_climb",
            re.compile(r"\brequest\b.*\bclimb\b|\brequest higher\b", re.I),
            "Pilot requests climb.",
        ),
        (
            "initial_checkin",
            re.compile(r"\b(?:passing|climbing|descending|level|with you)\b", re.I),
            "Pilot checks in with current altitude or climb/descent status.",
        ),
        (
            "readback_ack",
            re.compile(r"\b(?:squawk|heading|climb and maintain|descend and maintain|maintain|hold short|line up and wait|cleared for takeoff|cleared to)\b", re.I),
            "Pilot reads back the last instruction.",
        ),
    ]

    COMPLEX_PATTERNS = [
        re.compile(r"\bmayday\b|\bpan[\s-]?pan\b|\bemergency\b", re.I),
        re.compile(r"\bgo around\b|\bgoing around\b|\bmissed approach\b", re.I),
        re.compile(r"\bunable\b|\bcannot comply\b", re.I),
        re.compile(r"\brequest deviation\b|\bweather deviation\b", re.I),
        re.compile(r"\brequest holding\b|\bhold\b.*\bat\b", re.I),
        re.compile(r"\balternate\b|\bdivert\b", re.I),
        re.compile(r"\bengine\b.*\bfire\b|\bfailure\b|\bhydraulic\b|\belectrical\b", re.I),
    ]

    def __init__(self, config, llm_client):
        self.config = config
        self.llm_client = llm_client
        routing_cfg = config.get("ai_routing", {})
        self.light_model = (
            routing_cfg.get("light_model")
            or config.get("connection", {}).get("model")
            or "gemini-3.1-flash-lite-preview"
        )
        self.reasoning_model = routing_cfg.get("reasoning_model", "gemini-3.1-pro-preview")
        self.light_confidence_threshold = float(routing_cfg.get("light_confidence_threshold", 0.66))
        self.light_connection = self._connection_for("light")
        self.reasoning_connection = self._connection_for("reasoning")

    def update_config(self, new_config):
        self.__init__(new_config, self.llm_client)

    def _connection_for(self, tier):
        routing_cfg = self.config.get("ai_routing", {})
        sync = routing_cfg.get(f"{tier}_sync_with_primary", True)
        if sync:
            return None
        return {
            "provider": routing_cfg.get(f"{tier}_provider") or self.config.get("connection", {}).get("provider"),
            "api_key": routing_cfg.get(f"{tier}_api_key") or self.config.get("connection", {}).get("api_key"),
            "base_url": routing_cfg.get(f"{tier}_base_url") or self.config.get("connection", {}).get("base_url"),
        }

    def route(self, text, history=None):
        text = (text or "").strip()
        history = history or []
        if not text:
            return {"mode": "clarify", "reply": "Say again."}

        direct = self._match_rules(text)
        if direct:
            return direct

        semantic = self._semantic_parse(text, history)
        if semantic.get("needs_escalation"):
            return {
                "mode": "escalate",
                "normalized_text": semantic.get("normalized_text") or text,
                "reason": semantic.get("reason") or "light_model_escalation",
                "intent": semantic.get("intent") or "complex_request",
            }

        confidence = float(semantic.get("confidence") or 0.0)
        normalized_text = (semantic.get("normalized_text") or "").strip()
        if normalized_text and confidence >= self.light_confidence_threshold:
            return {
                "mode": "normalized",
                "normalized_text": normalized_text,
                "intent": semantic.get("intent") or "generic_request",
                "confidence": confidence,
            }

        return {
            "mode": "clarify",
            "reply": "Transmission unreadable, say again.",
            "intent": semantic.get("intent") or "unknown",
            "confidence": confidence,
        }

    def _match_rules(self, text):
        for pattern in self.COMPLEX_PATTERNS:
            if pattern.search(text):
                return {
                    "mode": "escalate",
                    "normalized_text": text,
                    "reason": "complex_phrase_detected",
                    "intent": "complex_request",
                }

        for intent, pattern, normalized in self.SIMPLE_RULES:
            if pattern.search(text):
                entities = self._extract_entities(text, intent)
                return {
                    "mode": "normalized",
                    "normalized_text": normalized,
                    "intent": intent,
                    "confidence": 0.95,
                    "entities": entities,
                }
        return None

    def _extract_entities(self, text, intent):
        text = text or ""
        entities = {}
        info = re.search(r"\b(?:information|atis)\s+([A-Z])\b", text, re.I)
        if info:
            entities["atis"] = info.group(1).upper()
        altitude = re.search(r"\b(?:passing|climbing(?: to)?|descending(?: to)?|maintain|flight level|fl)\s+(\d{2,5})\b", text, re.I)
        if altitude:
            entities["altitude"] = altitude.group(1)
        runway = re.search(r"\brunway\s+(\d{1,2}[LRC]?)\b", text, re.I)
        if runway:
            entities["runway"] = runway.group(1).upper()
        if intent == "readback_ack" and "hold short" in text.lower():
            entities["hold_short"] = True
        return entities

    def _semantic_parse(self, text, history):
        if not self.llm_client:
            return {}
        prompt = f"""
You are a semantic parser for pilot radio transmissions.
Return strict JSON only:
{{
  "intent": "one short snake_case intent",
  "normalized_text": "short normalized intent summary for downstream ATC generation",
  "confidence": 0.0,
  "needs_escalation": false,
  "reason": "short reason"
}}

Rules:
1. Do NOT answer as ATC.
2. Do NOT invent missing details.
3. If the text is unclear, set confidence below 0.5.
4. Set needs_escalation=true for emergency, go-around, unable, deviation, holding, diversion, or multi-part complex requests.
5. normalized_text must be short and explicit.
6. Keep confidence between 0 and 1.

Recent context:
{json.dumps(history[-4:], ensure_ascii=False)}
"""
        try:
            raw = self.llm_client._call_llm_sync(
                prompt,
                text,
                max_tokens=180,
                model_override=self.light_model,
                connection_override=self.light_connection,
            )
            match = re.search(r"\{.*\}", raw or "", re.S)
            payload = json.loads(match.group(0) if match else raw)
            return payload if isinstance(payload, dict) else {}
        except Exception as e:
            print(f"ATCIntentRouter: semantic parse failed: {e}")
            return {}
