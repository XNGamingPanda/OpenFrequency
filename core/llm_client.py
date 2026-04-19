import json
import copy
from google import genai
from google.genai import types
import openai

from core.china_airspace import is_in_china_airspace, build_china_rvsm_prompt_block
from core.cpdlc_manager import cpdlc_manager

class LLMClient:
    ROLE_RULES = {
        "Ground": {
            "duties": "Clearance Delivery, Pushback, Taxi instructions.",
            "taboos": "Do NOT give Takeoff/Landing clearances. Do NOT vector aircraft in air."
        },
        "Tower": {
            "duties": "Takeoff/Landing clearances, Runway crossing, Pattern entry.",
            "taboos": "Do NOT give complex taxi instructions to gates. Do NOT vector aircraft far from airport."
        },
        "Clearance Delivery": {
            "duties": "IFR clearance delivery, squawk assignment, departure clearance confirmation.",
            "taboos": "Do NOT issue takeoff or landing clearance. Do NOT provide radar vectors after departure."
        },
        "Approach/Departure": {
            "duties": "Radar vectors, Altitude assignments, ILS/Visual approach clearance.",
            "taboos": "Do NOT give ground taxi instructions. Do NOT clear for takeoff/landing (handoff to Tower)."
        },
        "Approach": {
            "duties": "Arrival sequencing, vectors, altitude assignments, ILS/visual approach clearance.",
            "taboos": "Do NOT give taxi instructions. Do NOT issue takeoff clearance."
        },
        "Departure": {
            "duties": "Departure radar vectors, climb instructions, SID transitions, handoff to Center.",
            "taboos": "Do NOT issue landing clearance. Do NOT issue gate or taxi instructions."
        },
        "Center": {
            "duties": "Enroute cruise, High altitude routing, Handoffs. When aircraft leaves your airspace, provide handoff frequency.",
            "taboos": "Do NOT give precision approach clearances. Do NOT give ground instructions."
        },
        "Unicom": {
            "duties": "Advisory only. State weather/traffic if asked.",
            "taboos": "Do NOT give CLEARANCES. You are NOT a controller."
        },
        "Emergency": {
            "duties": "Emergency assistance on 121.5MHz. Help with navigation, nearest airport, emergency procedures. Provide nearby ATC frequencies if requested.",
            "taboos": "Do NOT panic. Stay calm and professional. Prioritize safety."
        }
    }

    def __init__(self, config, context, lock, bus):
        self.config = config
        self.context = context
        self.lock = lock
        self.bus = bus
        
        conn_config = config.get('connection', {})
        self.provider = conn_config.get('provider', 'google_genai')
        self.api_key = conn_config.get('api_key', '')
        # Dual-model support:
        #   model_fast    — lightweight, low-latency (readbacks, simple instructions)
        #   model_thinking — full reasoning model (clearances, emergencies, complex routing)
        #   model          — legacy fallback used when fast/thinking not configured
        self.model         = conn_config.get('model', 'gemini-2.0-flash')
        self.model_fast    = conn_config.get('model_fast') or self.model
        self.model_thinking= conn_config.get('model_thinking') or self.model
        self.base_url = conn_config.get('base_url', None)

        print(f"LLMClient Debug: Provider='{self.provider}', API_Key_Present={bool(self.api_key)}, "
              f"Model(fast)='{self.model_fast}', Model(thinking)='{self.model_thinking}'")
        
        self.client = None
        self.openai_client = None

        if self.provider in ['google_genai', 'gemini']:
            print(f"LLMClient: Initializing Google GenAI Client with model {self.model}...")
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                print(f"LLMClient Error: Failed to initialize GenAI client: {e}")
        elif self.provider in ['openai', 'openai_compatible']:
            print(f"LLMClient: Initializing OpenAI Client ({self.provider}) with model {self.model}...")
            try:
                self.openai_client = openai.OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url
                )
            except Exception as e:
                print(f"LLMClient Error: Failed to initialize OpenAI client: {e}")
        
        self.bus.on('llm_request', self.handle_request)
        self.bus.on('proactive_atc_request', self.request_proactive_msg)
        self.bus.on('atc_monitor_check', self.handle_atc_monitor_check)
        self.bus.on('config_updated', self.handle_config_update)
        print("LLMClient: Initialized and subscribed to 'llm_request' & 'proactive_atc_request'.")
        
    def handle_config_update(self, new_config):
        """Re-initialize client when settings change."""
        print("LLMClient: Config updated, re-initializing client...")
        self.config = new_config
        conn_config = new_config.get('connection', {})
        self.provider = conn_config.get('provider', 'google_genai')
        self.api_key = conn_config.get('api_key', '')
        self.model          = conn_config.get('model', 'gemini-2.0-flash')
        self.model_fast     = conn_config.get('model_fast') or self.model
        self.model_thinking = conn_config.get('model_thinking') or self.model
        self.base_url = conn_config.get('base_url', None)

        print(f"LLMClient Update: Provider='{self.provider}', "
              f"fast='{self.model_fast}', thinking='{self.model_thinking}'")

        self.client = None
        self.openai_client = None

        if self.provider in ['google_genai', 'gemini']:
            print(f"LLMClient: Initializing Google GenAI Client with model {self.model}...")
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                print(f"LLMClient Error: Failed to initialize GenAI client: {e}")
        elif self.provider in ['openai', 'openai_compatible']:
            print(f"LLMClient: Initializing OpenAI Client ({self.provider}) with model {self.model}...")
            try:
                self.openai_client = openai.OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url
                )
            except Exception as e:
                print(f"LLMClient Error: Failed to initialize OpenAI client: {e}")

    def handle_request(self, user_text, history=[]):
        """Event handler for 'llm_request'."""
        # Handle dictionary input (text + metadata + callback)
        callback_event = None
        metadata = None
        
        if isinstance(user_text, dict):
            payload = user_text
            user_text = payload.get('text', '')
            callback_event = payload.get('callback_event')
            metadata = payload.get('metadata')
            # history might be in payload too, override if so
            if 'history' in payload:
                history = payload['history']
        
        print(f"LLMClient: Received request: '{user_text[:50]}...' (Callback: {callback_event})")
        
        # Run in thread to avoid blocking EventBus
        import threading
        t = threading.Thread(
            target=self.generate_response, 
            args=(user_text, None, False, history),
            kwargs={'callback_event': callback_event, 'metadata': metadata}
        )
        t.start()

    def request_proactive_msg(self, reason, context_snapshot):
        """
        Triggers the LLM to speak FIRST based on a system event.
        reason: str, e.g., "pilot_deviated_altitude" or "handoff_needed"
        context_snapshot: dict, current shared_context state
        """
        print(f"LLMClient: Generating PROACTIVE message for reason: {reason}")

        role = context_snapshot['atc_state']['current_controller']
        callsign = context_snapshot['aircraft']['callsign']
        atc_state = context_snapshot.get('atc_state', {})

        # Build extra context block for FIR crossing events
        fir_extra = ""
        if reason == 'pilot_crossed_fir_boundary_suggest_center_handoff':
            new_fir = atc_state.get('current_fir', '')
            fir_name = atc_state.get('current_fir_name', new_fir)
            fir_freq = atc_state.get('suggested_fir_freq', '')
            freq_str = f" on {fir_freq:.3f}" if fir_freq else ""
            fir_extra = (
                f"\n        FIR CROSSING DETAILS:"
                f"\n        - Aircraft has just entered {fir_name} ({new_fir})."
                f"\n        - Instruct pilot to contact the next center{freq_str}."
                f"\n        - Example: \"{callsign}, leaving our airspace. Contact {fir_name} Center{freq_str}, good day.\""
            )

        system_prompt = f"""
        You are {role}. The pilot ({callsign}) has triggered a system alert: "{reason}".

        Current Telemetry:
        - Alt: {context_snapshot['aircraft']['altitude']}
        - Hdg: {context_snapshot['aircraft']['heading']}
        {fir_extra}
        CRITICAL RULES:
        1. You are INITIATING contact. Do not wait for a reply.
        2. Keep it brief and authoritative.
        3. Use {callsign} to address the pilot.
        4. JSON Format: {{"text": "...", "action": "NONE"}}

        Generate the radio message now.
        """

        # Run in thread to avoid blocking EventBus/SimBridge
        import threading
        t = threading.Thread(target=self.generate_response, args=(None, system_prompt, True))
        t.start()

    def handle_atc_monitor_check(self, issue, context_snapshot):
        role = context_snapshot.get('atc_state', {}).get('current_controller', 'ATC')
        callsign = context_snapshot.get('aircraft', {}).get('callsign', 'Aircraft')
        aircraft = issue.get('aircraft', {})
        system_prompt = f"""
        You are {role}. Decide whether ATC should proactively contact {callsign}.

        Current possible issue:
        - Type: {issue.get('type')}
        - Detail: {issue.get('detail')}
        - Previous ATC instruction: {issue.get('instruction') or 'N/A'}
        - Aircraft: altitude {aircraft.get('altitude')} ft, heading {aircraft.get('heading')}, speed {aircraft.get('airspeed')} kt, vertical speed {aircraft.get('vs')} fpm, on ground {aircraft.get('on_ground')}, COM1 {aircraft.get('com1_freq')}

        Decision rules:
        1. If this is normal or not urgent, return exactly: {{"text": "", "action": "SILENT"}}
        2. If ATC should speak, return one short realistic radio call as JSON: {{"text": "...", "action": "ADVISORY"}}
        3. Do not over-control. Stay within the current controller role.
        4. Address the pilot by callsign {callsign}.
        """

        import threading
        t = threading.Thread(
            target=self.generate_response,
            args=(None, system_prompt, True),
            kwargs={
                'callback_event': 'atc_monitor_decision',
                'metadata': {'issue': issue}
            }
        )
        t.start()

    def _build_system_prompt(self, user_input, history=[]):
        """Dynamically builds the system prompt from the shared context."""
        import random
        
        with self.lock:
            context_copy = copy.deepcopy(self.context)

        role = context_copy['atc_state']['current_controller']
        callsign = context_copy['aircraft']['callsign']
        qnh = context_copy['environment']['qnh']
        nearest_airport = context_copy['environment'].get('nearest_airport', 'N/A')
        current_alt = context_copy['aircraft'].get('altitude', 0)
        flight_rules = context_copy.get('flight_rules', 'IFR')

        # Build "previously issued instructions" block — persists across frequency changes
        issued = context_copy.get('atc_state', {}).get('issued_instructions', {})
        issued_lines = []
        _label_map = {
            'squawk':           ('Squawk',           'Set transponder to this code'),
            'cleared_altitude': ('Cleared altitude',  'Aircraft is cleared to this altitude/FL'),
            'assigned_heading': ('Assigned heading',  'Aircraft is flying this heading'),
            'assigned_speed':   ('Assigned speed',    'Aircraft is maintaining this speed'),
            'altimeter':        ('Altimeter',         'Altimeter setting already provided'),
            'approach_clearance':('Approach',         'Approach clearance already issued'),
            'taxi_route':       ('Taxi route',        'Ground taxi route already issued'),
            'departure_runway': ('Departure runway',  'Runway already assigned'),
            'sid':              ('SID',               'Departure procedure already issued'),
        }
        for field, (lbl, _hint) in _label_map.items():
            val = issued.get(field)
            if val:
                issued_lines.append(f"  - {lbl}: {val}")

        if issued_lines:
            issued_text = (
                "PREVIOUSLY ISSUED INSTRUCTIONS FOR THIS AIRCRAFT "
                "(DO NOT re-issue unless pilot explicitly requests a change):\n"
                + "\n".join(issued_lines)
            )
        else:
            issued_text = ""
        
        # Flight Plan Info (condensed - only show essentials, not full route)
        fp = context_copy.get('flight_plan', {})
        fp_text = ""
        if fp.get('destination') != "N/A":
            # Extract just the SID if present in route
            route = fp.get('route', 'N/A')
            sid = route.split()[0] if route and route != 'N/A' else 'N/A'
            fp_text = f"""
        Flight Plan:
        - Origin: {fp.get('origin', 'N/A')}
        - Destination: {fp.get('destination', 'N/A')}
        - SID/Departure: {sid}
        - Cruise: {fp.get('cruise_alt', 'N/A')} FT
        """
        
        # Weather
        metar = context_copy['environment'].get('metar', 'N/A')
        current_frequency = context_copy['atc_state'].get('current_frequency', 0.0)
        current_frequency_label = context_copy['atc_state'].get('current_frequency_label', 'N/A')
        nearby_airports = context_copy['environment'].get('nearby_airports', [])
        current_airport = context_copy['environment'].get('current_airport', nearest_airport)
        ground_summary = context_copy.get('navigation', {}).get('ground_layout_summary', {}) or {}
        
        # === 频率数据库 (如无配置则随机生成) ===
        freq_db = dict(self.config.get('frequencies', {}))
        if nearby_airports:
            for airport in nearby_airports:
                if airport.get('ident') == current_airport:
                    for entry in airport.get('frequencies', []):
                        role_name = entry.get('role')
                        if role_name and role_name not in freq_db:
                            freq_db[role_name] = f"{float(entry.get('frequency_mhz', 0.0)):.3f}"
                    break
        if not freq_db:
            # 随机生成常用频率
            freq_db = {
                'Ground': f"121.{random.randint(70, 95)}",
                'Tower': f"118.{random.randint(10, 95)}",
                'Departure': f"119.{random.randint(10, 95)}",
                'Approach': f"124.{random.randint(10, 95)}",
                'Center': f"132.{random.randint(10, 95)}",
                'ATIS': f"127.{random.randint(10, 95)}",
                'Clearance Delivery': f"118.{random.randint(95, 99)}"
            }
        
        freq_text = f"""
        HANDOFF FREQUENCY DATABASE (Use when handing off pilot):
        - Clearance Delivery: {freq_db.get('Clearance Delivery', freq_db.get('Ground', '121.9'))}
        - Ground: {freq_db.get('Ground', '121.9')}
        - Tower: {freq_db.get('Tower', '118.1')}
        - Departure: {freq_db.get('Departure', '119.1')}
        - Approach: {freq_db.get('Approach', '124.65')}
        - Center: {freq_db.get('Center', '132.45')}
        - ATIS: {freq_db.get('ATIS', '127.25')}
        
        HANDOFF RULES:
        - When handing off, ALWAYS provide the next controller AND frequency.
        - Example: "Contact Departure on 119.1, goodbye."
        - If pilot ascending > 1500ft after takeoff: Suggest handoff to Departure.
        - If pilot descending < 5000ft on approach: Suggest handoff to Approach.
        - If pilot at cruise > FL180: Suggest handoff to Center.
        """
        
        # 应急频率增强
        emergency_help = ""
        if "Emergency" in role:
            emergency_help = f"""
        EMERGENCY ASSISTANCE RULES:
        - You are on 121.5 MHz Emergency frequency.
        - Provide calm, professional assistance.
        - If pilot asks for nearest airport: Suggest "{nearest_airport}" with Tower frequency {freq_db.get('Tower', '118.1')}.
        - If pilot asks for ATC help: Provide appropriate frequency from the database above.
        - Give vectors to nearest runway if possible.
        """

        ground_help = ""
        if "Ground" in role and ground_summary and ground_summary.get('airport_ident') == current_airport:
            suggested = ground_summary.get('suggested_taxi_route') or {}
            taxiway_names = ", ".join(ground_summary.get('taxiway_names', [])[:20]) or "N/A"
            stand_names = ", ".join(ground_summary.get('stand_names', [])[:25]) or "N/A"
            route_names = " -> ".join(suggested.get('taxiways', [])) if suggested.get('taxiways') else "N/A"
            route_target = suggested.get('target_runway') or suggested.get('end_node') or "N/A"
            ground_help = f"""
        GROUND LAYOUT DATA:
        - Source: {ground_summary.get('source', 'simulator')}
        - Airport: {ground_summary.get('airport_ident', current_airport)}
        - Taxiways known: {taxiway_names}
        - Stands/gates known: {stand_names}
        - Taxi graph: {ground_summary.get('taxi_node_count', 0)} nodes / {ground_summary.get('taxi_edge_count', 0)} edges
        - Suggested departure taxi route from current position: {route_names}
        - Suggested route target runway/holding point: {route_target}
        - Suggested route cost: {suggested.get('cost', 'N/A')}
        - Runway crossing count on suggested route: {suggested.get('runway_crossings', 'N/A')}

        GROUND MOVEMENT RULES:
        - Treat Suggested departure taxi route as the primary taxi plan when it is not N/A.
        - Use airport taxiway and stand names from the ground layout data when issuing taxi instructions.
        - Prefer routes with fewer runway crossings and fewer hotspots, even if slightly longer.
        - If the suggested route looks usable, issue taxi instructions close to that route instead of inventing random taxiway names.
        - Do not mention internal node IDs to the pilot; convert the path to taxiway names, runway holding point, and runway crossing instructions.
        """
        
        # ── China Metric RVSM block ───────────────────────────────────────────
        lat = context_copy['aircraft'].get('latitude', 0.0)
        lon = context_copy['aircraft'].get('longitude', 0.0)
        heading = context_copy['aircraft'].get('heading', 0)
        in_china = is_in_china_airspace(lat, lon)
        if in_china:
            china_rvsm_block = build_china_rvsm_prompt_block(track_deg=float(heading))
        else:
            china_rvsm_block = ""

        # ── CPDLC block (only for Center / high-altitude roles) ───────────────
        _cpdlc_roles = ('Center', 'Oceanic', 'CZQX', 'CZEG', 'CZWG', 'CZUL')
        if any(r in role for r in _cpdlc_roles) or cpdlc_manager.session_active:
            cpdlc_block = cpdlc_manager.build_prompt_block()
        else:
            cpdlc_block = ""

        # NOTE: History is now passed separately as messages, not embedded here
        # This saves token costs by using proper role-based messaging
        
        # Language-specific prompt injection
        stt_lang = self.config.get('audio', {}).get('stt_language', 'auto')
        allow_intl_non_english = self.config.get('immersion', {}).get('allow_non_english_intl', False)

        # Determine whether non-English comms are appropriate for current position
        use_native_lang = in_china or allow_intl_non_english

        if stt_lang == 'ja' and use_native_lang:
            # Japanese mode: Full Japanese ATC experience
            language_instruction = """
        6. LANGUAGE: Reply in JAPANESE (日本語) ONLY.
           - Use standard Japanese aviation phraseology.
           - Example: "JAL123, 離陸を許可します。滑走路34L。"
        """
        elif use_native_lang:
            # Chinese/English bilingual (China domestic or user opted in internationally)
            language_instruction = """
        3. Reply in the SAME LANGUAGE as the user (Chinese/English).
           - Chinese: "国航1024, 地面风310, 8节..."
           - English: "CCA1024, Wind 310 at 8 knots..."
        """
        else:
            # International standard: English only (ICAO realistic)
            language_instruction = """
        3. LANGUAGE: Reply in ENGLISH ONLY. Use standard ICAO phraseology.
           - Example: "CCA1024, wind 310 at 8 knots, runway 36L, cleared for takeoff."
           - Do NOT reply in Chinese, Japanese, or any other language.
        """
        
        # VFR-specific guidance rules
        if flight_rules == 'VFR':
            clearance_rule = """4. VFR OPERATIONS (pilot is flying VFR):
           - Do NOT issue IFR clearance or squawk codes (unless explicitly asked).
           - Use "VFR flight following" or "traffic advisories" instead of radar vectors.
           - Tower: issue "cleared for takeoff, make [left/right] traffic" or "cleared to land".
           - Departure/Approach: provide traffic advisories, altitude advisories, and frequency handoffs.
           - Center/Unicom: advise known traffic and weather; pilot is responsible for own navigation.
           - If weather appears IMC (low visibility/ceiling), advise the pilot and recommend IFR pickup.
           - Do NOT assign SIDs/STARs or complex instrument procedures."""
        else:
            clearance_rule = """4. IFR CLEARANCE RULE: When giving IFR clearance, ONLY say:
           - "Cleared to [DESTINATION] via [SID] departure, runway [RWY]. Squawk [CODE]."
           - Do NOT read out the full route waypoints. The SID name is enough."""

        prompt = f"""
        You are an advanced ATC AI.
        Role: {role} (Responsible for: Clearing, Ground Ops, Tower Control, or Approach/Center based on freq).
        User Callsign: {callsign}.
        Current Airport: {nearest_airport}
        Current Altitude: {current_alt} ft
        Tuned Frequency: {current_frequency or 'N/A'} MHz
        Tuned Channel: {current_frequency_label}
        Flight Rules: {flight_rules}

        DISPLAYED ROLE: {role}

        RULES FOR THIS POST:
        DUTIES: {self._get_role_rules(role)['duties']}
        TABOOS: {self._get_role_rules(role)['taboos']}

        Current Weather (METAR):
        {metar}

        {fp_text}

        {freq_text}

        {emergency_help}

        {ground_help}

        {issued_text}

        {china_rvsm_block}

        {cpdlc_block}

        CRITICAL RULES:
        1. Address the pilot by callsign '{callsign}' at the START of your message. Do NOT repeat it at the end.
        2. USE REAL WEATHER data from the METAR above.
        {language_instruction}
        {clearance_rule}
        5. Output JSON: {{"text": "...", "action": "NONE"}}
        6. READBACK HANDLING: If the pilot's readback is CORRECT, you do NOT need to say "Readback correct" every time.
           - You may return an empty string "" for text to remain silent (simulate 'click' acknowledgment).
           - Or just reply with the callsign "{callsign}" to acknowledge.
           - ONLY correct them if the readback is WRONG.
        7. PROACTIVE HANDOFFS: If pilot is in wrong airspace for your role, proactively suggest handoff with frequency.
        8. CONTINUITY: Use the PREVIOUSLY ISSUED INSTRUCTIONS above as the authoritative record.
           - Do NOT re-assign a squawk/altitude/heading unless the pilot explicitly asks for a change.
           - If the pilot asks "what was my squawk?" or similar, refer to the previously issued value.
           - When handing off to the next controller, assume they already received those instructions.
        """
        return prompt.strip()

    # ── Dual-model routing ────────────────────────────────────────────────────

    # Keywords that signal a COMPLEX request requiring the thinking model
    _THINKING_PATTERNS = [
        r'\b(emergency|mayday|pan.pan|declare)\b',
        r'\b(clearance|ifr clearance|cleared to|departure clearance)\b',
        r'\b(fl\d{2,3}|flight level)\b',
        r'\b(deviat|divert|unable|reroute|re-route)\b',
        r'\b(sid|star|approach|ils|rnav|vor approach)\b',
        r'\b(squawk|transponder)\b',
        r'\b(hold(ing)?( pattern| instructions)?)\b',
        r'\b(airspace|restricted|temporary flight restriction)\b',
    ]

    import re as _re

    @classmethod
    def _classify_complexity(cls, text: str) -> str:
        """
        Return 'thinking' for complex requests that benefit from a reasoning
        model, or 'fast' for simple acknowledgements / readbacks.
        """
        if not text:
            return 'fast'
        lower = text.lower()

        import re
        # Check thinking patterns first — even short messages can be complex
        # (e.g. "descend FL150", "mayday mayday")
        for pat in cls._THINKING_PATTERNS:
            if re.search(pat, lower):
                return 'thinking'

        # Very short messages with no thinking keywords → fast
        if len(text.split()) <= 5:
            return 'fast'

        return 'fast'

    def _select_model(self, user_text: str | None, is_proactive: bool) -> str:
        """Pick model_fast or model_thinking based on request complexity."""
        if is_proactive:
            # Proactive ATC messages (handoffs, alerts) use thinking model
            return self.model_thinking
        complexity = self._classify_complexity(user_text or '')
        model = self.model_fast if complexity == 'fast' else self.model_thinking
        print(f"LLMClient: Complexity='{complexity}' → model='{model}'")
        return model

    def _get_role_rules(self, full_role_name):
        """Extracts 'Ground', 'Tower', etc from 'ZBAA Ground' and returns rules."""
        for key in self.ROLE_RULES:
            if key in full_role_name:
                return self.ROLE_RULES[key]
        return {"duties": "General ATC assistance", "taboos": "None"}

    def _parse_llm_response(self, response_text):
        """Safely parses the expected JSON from the LLM, handling Markdown and extra text."""
        try:
            import re
            # Extract JSON block using regex (non-greedy)
            match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if match:
                json_str = match.group(0)
                data = json.loads(json_str)
                return data.get('text', response_text), data.get('action')
            
            # Fallback: Try cleaning markdown if regex missed
            clean_text = response_text.replace("```json", "").replace("```", "").strip()
            data = json.loads(clean_text)
            return data.get('text', response_text), data.get('action')
            
        except (json.JSONDecodeError, AttributeError):
            print(f"Warning: LLM response formatting failed. Raw: {response_text}")
            # If parsing fails, try to return just the text if it looks like a normal sentence,
            # otherwise return the raw output but log it.
            return response_text, None

    def _call_llm_sync(self, system_prompt, user_message, max_tokens=100):
        """Compatibility helper for older modules that need a synchronous plain-text reply."""
        if not self.client and not self.openai_client:
            raise RuntimeError("LLM client not initialized")

        try:
            if self.client:
                contents = [
                    types.Content(
                        role="user",
                        parts=[types.Part(text=system_prompt)]
                    ),
                    types.Content(
                        role="user",
                        parts=[types.Part(text=user_message)]
                    )
                ]
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(max_output_tokens=max_tokens)
                )
                return (response.text or "").strip()

            response = self.openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=max_tokens
            )
            return (response.choices[0].message.content or "").strip()
        except Exception:
            raise

    def generate_response(self, user_text=None, trigger_prompt=None, is_proactive=False, history=[], callback_event=None, metadata=None):
        if not self.client and not self.openai_client:
            print("LLMClient Error: Client not initialized.")
            return

        if is_proactive and trigger_prompt:
            system_prompt = trigger_prompt
        else:
            system_prompt = self._build_system_prompt(user_text, history=history)
        
        # Get callsign for fallback messages
        with self.lock:
            callsign = self.context.get('aircraft', {}).get('callsign', 'Station')
            
        print("--- Generated System Prompt ---")
        print(system_prompt)
        print("-----------------------------")
        
        print(f"LLMClient: Sending request to {self.model}...")
        
        # Pick fast or thinking model based on request complexity
        active_model = self._select_model(user_text, is_proactive)
        # Emit which model tier is being used so the dashboard can show it
        self.bus.emit('llm_model_selected', active_model)

        response_text = ""
        try:
            if self.client:
                # Google GenAI - Build proper contents with history
                contents = []
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(text=system_prompt)]
                ))
                for msg in history:
                    role = "user" if msg.get('sender') == 'Pilot' else "model"
                    contents.append(types.Content(
                        role=role,
                        parts=[types.Part(text=msg.get('text', ''))]
                    ))
                if user_text and not is_proactive:
                    contents.append(types.Content(
                        role="user",
                        parts=[types.Part(text=f"User said: {user_text}")]
                    ))

                gen_config_args = {}
                if "gemma" not in active_model.lower() and "thinking" not in active_model.lower():
                    gen_config_args["response_mime_type"] = "application/json"
                else:
                    print(f"LLMClient: Model '{active_model}' — JSON mode disabled.")

                response = self.client.models.generate_content(
                    model=active_model,
                    contents=contents,
                    config=types.GenerateContentConfig(**gen_config_args)
                )
                response_text = response.text

            elif self.openai_client:
                messages = [{"role": "system", "content": system_prompt}]
                for msg in history:
                    role = "user" if msg.get('sender') == 'Pilot' else "assistant"
                    messages.append({"role": role, "content": msg.get('text', '')})
                if user_text and not is_proactive:
                    messages.append({"role": "user", "content": user_text})

                use_json = ("json" in active_model.lower() or "gpt" in active_model.lower())
                response = self.openai_client.chat.completions.create(
                    model=active_model,
                    messages=messages,
                    response_format={"type": "json_object"} if use_json else None
                )
                response_text = response.choices[0].message.content

            print(f"LLM Raw Response: {response_text}")
            
            # If callback event is specified (e.g. for landing review), emit raw response to callback
            if callback_event:
                print(f"LLMClient: Emitting to callback '{callback_event}' instead of global broadcast.")
                self.bus.emit(callback_event, response_text, metadata or {})
                return

            text, action = self._parse_llm_response(response_text)
            
        except Exception as e:
            import traceback
            err_msg = traceback.format_exc()
            # Check for 503 Overloaded
            if "503" in str(e) or "overloaded" in str(e).lower():
                print(f"LLMClient: Service Overloaded (503). Switching to immersive fallback.")
                text = f"{callsign}, Station calling, signal unreadable, say again? (Simulated Interference)"
                action = "NONE"
            else:
                print(f"Error calling LLM: {e}")
                print(err_msg)
                try:
                    with open("llm_error.txt", "w", encoding="utf-8") as f:
                        f.write(err_msg)
                    print("Traceback written to llm_error.txt")
                except:
                    pass
                text = f"{callsign}, system error, standby."
                action = "NONE"
                
                # For callback on error, still emit something
                if callback_event:
                    self.bus.emit(callback_event, "{}", metadata or {})
                    return

        print(f"LLMClient: Emitting response - Text: '{text[:50]}...', Action: {action}")
        self.bus.emit('llm_response_generated', text, action)
