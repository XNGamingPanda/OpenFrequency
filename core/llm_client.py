import json
import copy
from google import genai
from google.genai import types
import openai

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
        "Approach/Departure": {
            "duties": "Radar vectors, Altitude assignments, ILS/Visual approach clearance.",
            "taboos": "Do NOT give ground taxi instructions. Do NOT clear for takeoff/landing (handoff to Tower)."
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
        self.model = conn_config.get('model', 'gemini-3-flash-preview')
        self.base_url = conn_config.get('base_url', None)

        print(f"LLMClient Debug: Provider='{self.provider}', API_Key_Present={bool(self.api_key)}, Model='{self.model}'")
        
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
        self.bus.on('config_updated', self.handle_config_update)
        print("LLMClient: Initialized and subscribed to 'llm_request' & 'proactive_atc_request'.")
        
    def handle_config_update(self, new_config):
        """Re-initialize client when settings change."""
        print("LLMClient: Config updated, re-initializing client...")
        self.config = new_config
        conn_config = new_config.get('connection', {})
        self.provider = conn_config.get('provider', 'google_genai')
        self.api_key = conn_config.get('api_key', '')
        self.model = conn_config.get('model', 'gemini-3-flash-preview')
        self.base_url = conn_config.get('base_url', None)

        print(f"LLMClient Update Debug: Provider='{self.provider}', API_Key_Present={bool(self.api_key)}, Model='{self.model}'")

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
        
        system_prompt = f"""
        You are {role}. The pilot ({callsign}) has triggered a system alert: "{reason}".
        
        Current Telemetry:
        - Alt: {context_snapshot['aircraft']['altitude']}
        - Hdg: {context_snapshot['aircraft']['heading']}
        
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
        
        # === 频率数据库 (如无配置则随机生成) ===
        freq_db = self.config.get('frequencies', {})
        if not freq_db:
            # 随机生成常用频率
            freq_db = {
                'Ground': f"121.{random.randint(70, 95)}",
                'Tower': f"118.{random.randint(10, 95)}",
                'Departure': f"119.{random.randint(10, 95)}",
                'Approach': f"124.{random.randint(10, 95)}",
                'Center': f"132.{random.randint(10, 95)}",
                'ATIS': f"127.{random.randint(10, 95)}"
            }
        
        freq_text = f"""
        HANDOFF FREQUENCY DATABASE (Use when handing off pilot):
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
        
        # NOTE: History is now passed separately as messages, not embedded here
        # This saves token costs by using proper role-based messaging
        
        # Language-specific prompt injection
        stt_lang = self.config.get('audio', {}).get('stt_language', 'auto')
        
        if stt_lang == 'ja':
            # Japanese mode: Full Japanese ATC experience
            language_instruction = """
        6. LANGUAGE: Reply in JAPANESE (日本語) ONLY.
           - Use standard Japanese aviation phraseology.
           - Example: "JAL123, 離陸を許可します。滑走路34L。"
        """
        else:
            # Default: Chinese/English bilingual
            language_instruction = """
        3. Reply in the SAME LANGUAGE as the user (Chinese/English).
           - Chinese: "国航1024, 地面风310, 8节..."
           - English: "CCA1024, Wind 310 at 8 knots..."
        """
        
        prompt = f"""
        You are an advanced ATC AI.
        Role: {role} (Responsible for: Clearing, Ground Ops, Tower Control, or Approach/Center based on freq).
        User Callsign: {callsign}.
        Current Airport: {nearest_airport}
        Current Altitude: {current_alt} ft
        
        DISPLAYED ROLE: {role}
        
        RULES FOR THIS POST:
        DUTIES: {self._get_role_rules(role)['duties']}
        TABOOS: {self._get_role_rules(role)['taboos']}
        
        Current Weather (METAR):
        {metar}
        
        {fp_text}
        
        {freq_text}
        
        {emergency_help}
        
        CRITICAL RULES:
        1. Address the pilot by callsign '{callsign}' at the START of your message. Do NOT repeat it at the end.
        2. USE REAL WEATHER data from the METAR above.
        {language_instruction}
        4. IFR CLEARANCE RULE: When giving IFR clearance, ONLY say:
           - "Cleared to [DESTINATION] via [SID] departure, runway [RWY]. Squawk [CODE]."
           - Do NOT read out the full route waypoints. The SID name is enough.
        5. Output JSON: {{"text": "...", "action": "NONE"}}
        6. READBACK HANDLING: If the pilot's readback is CORRECT, you do NOT need to say "Readback correct" every time. 
           - You may return an empty string "" for text to remain silent (simulate 'click' acknowledgment).
           - Or just reply with the callsign "{callsign}" to acknowledge.
           - ONLY correct them if the readback is WRONG.
        7. PROACTIVE HANDOFFS: If pilot is in wrong airspace for your role, proactively suggest handoff with frequency.
        """
        return prompt.strip()

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
        
        response_text = ""
        try:
            if self.client:
                # Google GenAI - Build proper contents with history
                # Format: system prompt as first content, then history as user/model turns
                contents = []
                
                # System instruction
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(text=system_prompt)]
                ))
                
                # Add history as proper turns (saves tokens vs embedding in prompt)
                for msg in history:
                    role = "user" if msg.get('sender') == 'Pilot' else "model"
                    contents.append(types.Content(
                        role=role,
                        parts=[types.Part(text=msg.get('text', ''))]
                    ))
                
                # Add current user input
                if user_text and not is_proactive:
                    contents.append(types.Content(
                        role="user",
                        parts=[types.Part(text=f"User said: {user_text}")]
                    ))
                
                # Conditional config based on model
                gen_config_args = {}
                if "gemma" not in self.model.lower():
                    gen_config_args["response_mime_type"] = "application/json"
                else:
                    print(f"LLMClient: Model '{self.model}' detected. Disabling strict JSON mode enforcement.")

                response = self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(**gen_config_args)
                )
                response_text = response.text
                
            elif self.openai_client:
                # OpenAI / Compatible API Call - Use proper messages array
                messages = [{"role": "system", "content": system_prompt}]
                
                # Add history as proper user/assistant turns
                for msg in history:
                    role = "user" if msg.get('sender') == 'Pilot' else "assistant"
                    messages.append({"role": role, "content": msg.get('text', '')})
                
                # Add current user input
                if user_text and not is_proactive:
                    messages.append({"role": "user", "content": user_text})
                
                response = self.openai_client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    response_format={"type": "json_object"} if "json" in self.model.lower() or "gpt" in self.model.lower() else None
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