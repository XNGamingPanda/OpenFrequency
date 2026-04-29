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
            "duties": "Clearance Delivery, Pushback, Taxi instructions. State QNH once in initial clearance only.",
            "taboos": "Do NOT give Takeoff/Landing clearances. Do NOT vector aircraft in air. Do NOT repeat QNH in every readback response — say it once."
        },
        "Tower": {
            "duties": "Takeoff/Landing clearances, Runway crossing, Pattern entry. Issue initial climb only up to pattern altitude (≤3000 ft). Handoff to Departure after takeoff.",
            "taboos": "Do NOT assign cruise/enroute altitudes above 3000 ft. Do NOT give ATIS information or QNH to departing aircraft. When issuing a handoff, ONLY say 'contact [facility] on [freq], good day' — nothing else."
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
            "duties": "Arrival sequencing, vectors, altitude assignments, ILS/visual approach clearance. Give QNH on first contact during arrival only.",
            "taboos": "Do NOT give taxi instructions. Do NOT issue takeoff clearance. Do NOT give ATIS or arrival sequences to aircraft that are clearly DEPARTING (climbing away, high altitude). If a departing aircraft checks in, immediately hand them to Center/Departure."
        },
        "Departure": {
            "duties": "Departure radar vectors, climb instructions, SID transitions, handoff to Center.",
            "taboos": "Do NOT issue landing clearance. Do NOT issue gate or taxi instructions."
        },
        "Center": {
            "duties": "Enroute cruise, High altitude routing, Handoffs. When aircraft leaves your airspace, provide handoff frequency.",
            "taboos": "Do NOT give precision approach clearances. Do NOT give ground instructions. NEVER use QNH or altimeter — use Flight Level (FL) for all altitude references above FL180. Do NOT command an aircraft descending from cruise altitude unless specifically requested by the pilot."
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

    def __init__(self, config, context, lock, bus, airport_frequency_service=None):
        self.config = config
        self.context = context
        self.lock = lock
        self.bus = bus
        self.airport_frequency_service = airport_frequency_service
        
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

        ac = context_snapshot.get('aircraft', {})
        alt = ac.get('altitude', 0)
        vs = ac.get('vs', 0)
        on_ground = ac.get('on_ground', False)
        flight_phase = "on ground" if on_ground else ("climbing" if vs > 200 else ("descending" if vs < -200 else "level cruise"))

        # Language instruction for proactive messages
        lat_p = ac.get('latitude', 0.0)
        lon_p = ac.get('longitude', 0.0)
        in_china_p = is_in_china_airspace(lat_p, lon_p)
        allow_intl = self.config.get('immersion', {}).get('allow_non_english_intl', False)
        stt_lang_p = self.config.get('audio', {}).get('stt_language', 'auto')
        if stt_lang_p == 'ja' and (in_china_p or allow_intl):
            pro_lang = "Reply in JAPANESE only. Use standard Japanese aviation phraseology."
        elif in_china_p or allow_intl:
            pro_lang = "使用中文回复（REPLY IN CHINESE ONLY）。使用中国民航标准管制用语。"
        else:
            pro_lang = "Reply in ENGLISH only. Use standard ICAO phraseology."

        system_prompt = f"""
        You are {role}. The pilot ({callsign}) has triggered a system alert: "{reason}".
        LANGUAGE: {pro_lang}

        Current Telemetry:
        - Alt: {alt} ft  |  VS: {vs} fpm  |  Flight phase: {flight_phase}
        - Hdg: {ac.get('heading', 'N/A')}
        {fir_extra}
        CRITICAL RULES:
        1. You are INITIATING contact. Do not wait for a reply.
        2. Keep it brief and authoritative — one radio call only.
        3. Use {callsign} to address the pilot.
        4. Match the handoff/instruction to the actual flight phase above (climbing → Departure/Center; descending → Approach).
        5. JSON Format: {{"text": "...", "action": "NONE"}}

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
        ac_lat = context_snapshot.get('aircraft', {}).get('latitude', 0.0)
        ac_lon = context_snapshot.get('aircraft', {}).get('longitude', 0.0)
        in_china_m = is_in_china_airspace(ac_lat, ac_lon)
        allow_intl_m = self.config.get('immersion', {}).get('allow_non_english_intl', False)
        mon_lang = "使用中文回复。使用中国民航标准管制用语。" if (in_china_m or allow_intl_m) else "Reply in ENGLISH only."
        _flight_rules  = context_snapshot.get('flight_rules', 'IFR')
        _nearest_arpt  = context_snapshot.get('environment', {}).get('nearest_airport', 'N/A')
        _metar         = context_snapshot.get('environment', {}).get('metar', 'N/A')

        # Issue-specific guidance snippets injected into the prompt
        _issue_type = issue.get('type', '')
        _issue_guidance = ""
        if _issue_type == "radar_contact_initial":
            _issue_guidance = (
                "\n        RADAR CONTACT GUIDANCE: Issue a brief 'radar contact' call on this new frequency. "
                "For VFR flight following say: '{callsign}, radar contact, [position], VFR flight following approved. "
                "Squawk [code] if desired. Altimeter [setting].' Keep it brief — do NOT deliver a full clearance."
            ).format(callsign=callsign)
        elif _issue_type == "vfr_base_final_no_clearance":
            _issue_guidance = (
                "\n        VFR PATTERN GUIDANCE: Aircraft appears on base or final without a landing clearance. "
                "Issue landing clearance or sequence instruction immediately: "
                "'{callsign}, runway XX, cleared to land.' or '{callsign}, number 2 follow [traffic], "
                "runway XX, cleared to land.' Do NOT issue an IFR approach clearance."
            ).format(callsign=callsign)
        elif _issue_type == "traffic_proximity_conflict":
            _issue_guidance = (
                "\n        TRAFFIC ADVISORY GUIDANCE: Two aircraft are converging. Issue a TCAS/traffic advisory: "
                "'{callsign}, traffic [bearing] o'clock, [distance] miles, [altitude], [direction/type].' "
                "Keep it concise. Do NOT issue a deviation heading unless separation is critically threatened."
            ).format(callsign=callsign)
        elif _issue_type == "vfr_pattern_high_departure":
            _issue_guidance = (
                "\n        PATTERN DEPARTURE GUIDANCE: Aircraft climbing higher than expected after takeoff with "
                "make-traffic instruction. Query intentions: '{callsign}, say intentions — departing the pattern "
                "or remaining in the pattern?' If departing, issue frequency change."
            ).format(callsign=callsign)

        system_prompt = f"""
        You are {role}. Decide whether ATC should proactively contact {callsign}.
        LANGUAGE: {mon_lang}
        Flight rules: {_flight_rules}
        Nearest airport: {_nearest_arpt}
        Weather (METAR): {_metar}

        Current possible issue:
        - Type: {_issue_type}
        - Detail: {issue.get('detail')}
        - Previous ATC instruction: {issue.get('instruction') or 'N/A'}
        - Aircraft: altitude {aircraft.get('altitude')} ft, heading {aircraft.get('heading')}, speed {aircraft.get('airspeed')} kt, vertical speed {aircraft.get('vs')} fpm, on ground {aircraft.get('on_ground')}, COM1 {aircraft.get('com1_freq')}
        {_issue_guidance}
        Decision rules:
        1. If this is normal or not urgent, return exactly: {{"text": "", "action": "SILENT"}}
        2. If ATC should speak, return one short realistic radio call as JSON: {{"text": "...", "action": "ADVISORY"}}
        3. Do not over-control. Stay within the current controller role.
        4. Address the pilot by callsign {callsign}.
        5. NEVER use QNH in Center/enroute context — use Flight Level only.
        6. For VFR operations: traffic advisories and pattern calls only — no IFR procedures.
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
        
        # === 频率数据库 — 严格来自机场数据库，拒绝范围外频率，不使用硬编码兜底 ===
        _ROLE_FREQ_RANGES = {
            'Ground':             (121.600, 121.975),
            'Clearance Delivery': (118.000, 121.975),
            'Tower':              (118.000, 136.000),
            'Departure':          (119.000, 136.000),
            'Approach':           (118.000, 136.000),
            'Center':             (119.000, 136.000),
            'ATIS':               (108.000, 136.000),
        }
        def _load_airport_freqs(airport_ident, db_out):
            """Load validated frequencies for one airport into db_out (no overwrite)."""
            if not self.airport_frequency_service or not airport_ident:
                return
            # First try nearby_airports cache (fast path)
            for ap in nearby_airports:
                if ap.get('ident') == airport_ident:
                    for entry in ap.get('frequencies', []):
                        role_name = entry.get('role')
                        freq_val = float(entry.get('frequency_mhz') or 0.0)
                        if not role_name or role_name in db_out or freq_val <= 0:
                            continue
                        lo, hi = _ROLE_FREQ_RANGES.get(role_name, (118.0, 136.0))
                        if lo <= freq_val <= hi:
                            db_out[role_name] = f"{freq_val:.3f}"
                        else:
                            print(f"LLMClient: Rejected {role_name} {freq_val:.3f} for {airport_ident} — outside [{lo},{hi}]")
                    return
            # Slow path: direct CSV lookup (handles case where nearby_airports is stale)
            try:
                entries = self.airport_frequency_service.get_airport_frequencies(airport_ident)
                for entry in entries:
                    role_name = entry.get('role')
                    freq_val = float(entry.get('frequency_mhz') or 0.0)
                    if not role_name or role_name in db_out or freq_val <= 0:
                        continue
                    lo, hi = _ROLE_FREQ_RANGES.get(role_name, (118.0, 136.0))
                    if lo <= freq_val <= hi:
                        db_out[role_name] = f"{freq_val:.3f}"
            except Exception as e:
                print(f"LLMClient: freq CSV lookup failed for {airport_ident}: {e}")

        freq_db = dict(self.config.get('frequencies', {}))
        # Primary: current airport (where the aircraft is now)
        _load_airport_freqs(current_airport, freq_db)
        # Fallback: destination airport — covers "arrived but current_airport not yet updated"
        dest = context_copy.get('flight_plan', {}).get('destination', '')
        if dest and dest != current_airport and len(freq_db) < 4:
            _load_airport_freqs(dest, freq_db)

        def _f(role):
            return freq_db[role] if role in freq_db else "UNKNOWN"

        freq_text = f"""
        HANDOFF FREQUENCY DATABASE for {current_airport}:
        - Clearance Delivery: {_f('Clearance Delivery')}
        - Ground:             {_f('Ground')}
        - Tower:              {_f('Tower')}
        - Departure:          {_f('Departure')}
        - Approach:           {_f('Approach')}
        - Center:             {_f('Center')}
        - ATIS:               {_f('ATIS')}

        HANDOFF RULES (CRITICAL — follow exactly):
        - ONLY use frequencies from this database. UNKNOWN means the frequency is not available —
          do NOT invent one. Say "Contact [Role], good day." without a frequency.
        - NEVER hand off to a role that does not match the current flight phase.
          Ground is for taxiing. Departure is only after takeoff. Center is only at cruise altitude.
          Approach is only when descending toward the destination.
        - Issue each handoff instruction ONCE. Do not repeat it in the same or subsequent messages.
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

        GROUND MOVEMENT RULES (CRITICAL):
        - When "Suggested departure taxi route" is NOT N/A, use it VERBATIM — copy the taxiway names exactly as listed.
          Do NOT rename, reorder, abbreviate, or substitute any taxiway name.
        - ONLY use taxiway names from "Taxiways known" list above. NEVER invent names not in that list.
        - If the suggested route is N/A, say "taxi to holding point, follow marshaller / follow signage" — do NOT guess a route.
        - Do NOT mention internal node IDs; say only taxiway names, holding point, and runway number.
        - Runway crossings: tell pilot to "cross runway XX" explicitly for each runway on route.
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
            language_instruction = """
        6. LANGUAGE: Reply in JAPANESE (日本語) ONLY. Use standard Japanese aviation phraseology.
           Clearance example:    "JAL123、管制承認を通報します。目的地東京、経路…"
           Takeoff example:      "JAL123、滑走路34L、離陸を許可します。"
           Handoff example:      "JAL123、東京コントロール、133.0へどうぞ。"
        """
        elif use_native_lang:
            language_instruction = """
        3. LANGUAGE: 使用中文回复（REPLY IN CHINESE）。使用中国民航标准管制用语。
           放行示例: "{callsign}，可以预推，预计跑道36L，离场程序PIKAS一号，应答机{squawk}，巡航高度9200米。"
           地面示例: "{callsign}，地面管制，可以开车，停机位X号，经由A滑行道，等待跑道36L外。"
           塔台示例: "{callsign}，地面风310度，8节，36左跑道，可以起飞。"
           移交示例: "{callsign}，联系离场，频率119.1，再见。"
           下降示例: "{callsign}，下降到3000米，QNH1013，联系进近，频率124.3，再见。"
           - 数字读法：高度用"米"或"飞行高度层"，风向"310度"，频率"一一九点一"。
           - 跑道后缀：L读"左"，R读"右"，C读"中"。
           - 回答用语：收到→"明白"，确认→"证实"，等待→"稍等"。
        """.format(callsign=callsign, squawk='####')
        else:
            language_instruction = """
        3. LANGUAGE: Reply in ENGLISH ONLY. Use standard ICAO phraseology.
           Clearance example: "CCA1024, cleared to Beijing via PIKAS departure, runway 36L. Squawk 2341."
           Tower example:     "CCA1024, wind 310 at 8 knots, runway 36L, cleared for takeoff."
           Handoff example:   "CCA1024, contact Departure on 119.1, good day."
           - Do NOT reply in Chinese, Japanese, or any other language.
        """
        
        # VFR-specific guidance rules
        if flight_rules == 'VFR':
            # ── Derive density-altitude advisory ─────────────────────────────
            _oat_c   = context_copy.get('environment', {}).get('oat_c', None)    # °C
            _elev_ft = context_copy.get('environment', {}).get('elevation_ft', 0)
            _qnh_hpa = qnh or 1013
            _da_hint = ""
            if _oat_c is not None:
                try:
                    # Standard temp at elevation: 15 - 1.98°C per 1000ft
                    _isa_temp   = 15.0 - 1.98 * (_elev_ft / 1000.0)
                    _temp_dev   = _oat_c - _isa_temp
                    _press_alt  = _elev_ft + (1013 - _qnh_hpa) * 30
                    _density_alt = _press_alt + 120 * _temp_dev
                    if _density_alt > _elev_ft + 1000:
                        _da_hint = (
                            f"\n   - ⚠️ DENSITY ALTITUDE WARNING: Field elev {_elev_ft:.0f} ft, "
                            f"OAT {_oat_c:.0f}°C, density altitude ~{_density_alt:.0f} ft. "
                            "Advise the pilot of reduced performance on hot/high days."
                        )
                except Exception:
                    pass

            # ── Derive IMC / marginal VMC advisory ───────────────────────────
            _metar_lower = (metar or "").lower()
            _imc_hint = ""
            # Check for low visibility or ceiling keywords in METAR
            import re as _re_vfr
            _vis_match = _re_vfr.search(r'\b(\d{4})\b', metar or "")  # ICAO vis in metres
            _ceiling_match = _re_vfr.search(r'\b(bkn|ovc)(\d{3})\b', _metar_lower)
            if _ceiling_match:
                _ceiling_ft = int(_ceiling_match.group(2)) * 100
                if _ceiling_ft < 1500:
                    _imc_hint = (
                        f"\n   - ⚠️ LOW CEILING: METAR indicates ceiling {_ceiling_ft} ft AGL. "
                        "VFR may be marginal or IMC — advise pilot to consider IFR pickup if conditions "
                        "deteriorate below VFR minimums (typically 1000 ft ceiling / 3 SM visibility)."
                    )

            # ── VFR cruise altitude hemisphere rule ───────────────────────────
            _hdg = context_copy['aircraft'].get('heading', 0)
            if 0 <= _hdg < 180:  # eastbound (magnetic 0-179)
                _vfr_odd_even = "ODD thousands + 500 ft (3500, 5500, 7500…) for eastbound VFR"
            else:
                _vfr_odd_even = "EVEN thousands + 500 ft (4500, 6500, 8500…) for westbound VFR"

            clearance_rule = f"""4. VFR OPERATIONS (pilot is flying VFR):
           - Do NOT issue IFR clearance, squawk codes, SIDs, STARs, or instrument procedures (unless explicitly requested).
           - Use "VFR flight following" or "traffic advisories" phrasing instead of radar vectors.
           - INTENTIONS: If the pilot hasn't stated intentions, ask: "{callsign}, state intentions and destination."
           - TRAFFIC PATTERN (Tower):
             * Takeoff: "Runway XX, cleared for takeoff, make [left/right] traffic." Specify pattern direction.
             * Upwind → crosswind → downwind → base → final. Call "Cleared to land, runway XX" on final.
             * If touch-and-go: "Cleared touch-and-go, runway XX, make [left/right] traffic."
             * Sequence: "Number [2/3] following [traffic description], report [downwind/base/final]."
           - DEPARTURE/APPROACH: Issue traffic advisories, altitude advisories, and frequency handoffs.
             Do NOT assign instrument departures. Advise: "VFR traffic advisories available, remain clear of Class B/C/D."
           - AIRSPACE ADVISORIES:
             * Class B: "Radar contact, VFR flight following approved. Remain clear of [airport] Class B unless cleared."
             * Class C/D: "Squawk [code], ident. Report 5 miles out on this frequency."
             * Unicom (CTAF): "Traffic advisory — [callsign], position/altitude — announce intentions on [freq]."
           - VFR ALTITUDE RULE: {_vfr_odd_even} (FAR 91.159 / ICAO cruising altitude rule).
             Suggest appropriate altitude when pilot requests cruise altitude.{_da_hint}{_imc_hint}
           - CENTER/ENROUTE: Advise known traffic and weather. Pilot is responsible for own navigation.
             "Radar contact, traffic advisory, [callsign], [bearing/distance], [altitude], [direction]."
           - IMC CHECK: If weather appears IMC or marginal VMC, proactively advise and offer IFR pickup:
             "Pilot reports VFR, current conditions appear marginal. Recommend IFR pickup if unable to maintain VMC."
           - Do NOT over-control VFR flights — advise, don't direct."""
            clearance_rule = """4. IFR CLEARANCE RULE: When giving IFR clearance, ONLY say:
           - "Cleared to [DESTINATION] via [SID] departure, runway [RWY]. Squawk [CODE]."
           - Do NOT read out the full route waypoints. The SID name is enough."""

        # ── Readback detection ────────────────────────────────────────────────────
        # If the pilot's message contains a frequency found in the last ATC message,
        # it is almost certainly a readback — flag it so the LLM stays silent.
        readback_hint = ""
        if user_input and history:
            import re as _re2
            last_atc_msgs = [m for m in history if m.get('sender') not in ('Pilot', 'SYSTEM')]
            if last_atc_msgs:
                last_atc_text = last_atc_msgs[-1].get('text', '')
                freqs_in_atc = set(_re2.findall(r'\b1[1-3]\d\.\d{1,3}\b', last_atc_text))
                freqs_in_pilot = set(_re2.findall(r'\b1[1-3]\d\.\d{1,3}\b', user_input))
                if freqs_in_atc & freqs_in_pilot:
                    shared_freq = ', '.join(freqs_in_atc & freqs_in_pilot)
                    readback_hint = f"\n        ⚠️ READBACK DETECTED: The pilot's message contains frequency {shared_freq} that you just issued. This is a readback, NOT a new request. Return EMPTY text or just '{callsign}' to acknowledge silently. Do NOT re-issue the instruction."

        prompt = f"""
        You are an advanced ATC AI.
        {language_instruction}

        Role: {role}
        User Callsign: {callsign}.
        Current Airport: {nearest_airport}
        Current Altitude: {current_alt} ft
        Tuned Frequency: {current_frequency or 'N/A'} MHz
        Tuned Channel: {current_frequency_label}
        Flight Rules: {flight_rules}

        SELF-IDENTIFICATION RULE: When you identify yourself in transmissions, use ONLY "{role}" — never a different facility name.
        Example: if role is "Shanghai Center", say "Shanghai Center" — NOT "Shanghai Approach" or any other name.

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
        {readback_hint}
        CRITICAL RULES:
        1. Address the pilot by callsign '{callsign}' at the START of your message. Do NOT repeat it at the end.
        2. USE REAL WEATHER data from the METAR above.
        3. Output JSON: {{"text": "...", "action": "NONE"}}
        4. READBACK HANDLING: If the pilot's message repeats a frequency/altitude/instruction you just gave, it is a readback.
           - Return EMPTY string "" or just "{callsign}" to acknowledge silently.
           - Do NOT re-issue the same handoff or instruction. Issue each instruction ONCE only.
           - ONLY speak if the readback is WRONG.
        5. {clearance_rule}
        6. PROACTIVE HANDOFFS: If pilot is in wrong airspace for your role, proactively suggest handoff with frequency.
        7. CONTINUITY: Use the PREVIOUSLY ISSUED INSTRUCTIONS above as the authoritative record.
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
