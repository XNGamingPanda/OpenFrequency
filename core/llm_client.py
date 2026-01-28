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
            "duties": "Enroute cruise, High altitude routing, Handoffs.",
            "taboos": "Do NOT give precision approach clearances. Do NOT give ground instructions."
        },
        "Unicom": {
            "duties": "Advisory only. State weather/traffic if asked.",
            "taboos": "Do NOT give CLEARANCES. You are NOT a controller."
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
        print(f"LLMClient: Received request: '{user_text}' (History len: {len(history)})")
        self.generate_response(user_text, history=history)

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
        
        self.generate_response(trigger_prompt=system_prompt, is_proactive=True)

    def _build_system_prompt(self, user_input, history=[]):
        """Dynamically builds the system prompt from the shared context."""
        with self.lock:
            context_copy = copy.deepcopy(self.context)

        role = context_copy['atc_state']['current_controller']
        callsign = context_copy['aircraft']['callsign']
        qnh = context_copy['environment']['qnh']
        
        # Flight Plan Info
        fp = context_copy.get('flight_plan', {})
        fp_text = ""
        if fp.get('destination') != "N/A":
            fp_text = f"""
        Flight Plan:
        - Origin: {fp.get('origin', 'N/A')}
        - Destination: {fp.get('destination', 'N/A')}
        - Route: {fp.get('route', 'N/A')}
        - Cruise: {fp.get('cruise_alt', 'N/A')} FT
        """
        
        # Weather
        metar = context_copy['environment'].get('metar', 'N/A')

        # Format History
        # Filter out the very last message if it matches user_input to avoid redundancy?
        # Only keep messages that are NOT the current input.
        # history is list of {'sender':..., 'text':...}
        history_text = ""
        if history:
            history_text = "Recent Conversation Log:\n"
            for msg in history:
                # Skip if it looks like the current input (simple check)
                if msg['text'] == user_input and msg['sender'] == 'Pilot':
                    continue
                history_text += f"{msg['sender']}: {msg['text']}\n"
        
        prompt = f"""
        You are an advanced ATC AI.
        Role: {role} (Responsible for: Clearing, Ground Ops, Tower Control, or Approach/Center based on freq).
        User Callsign: {callsign}.
        
        DISPLAYED ROLE: {role}
        
        RULES FOR THIS POST:
        DUTIES: {self._get_role_rules(role)['duties']}
        TABOOS: {self._get_role_rules(role)['taboos']}
        
        Current Weather (METAR):
        {metar}
        
        {fp_text}
        
        {history_text}
        
        CRITICAL RULES:
        1. Address the pilot by callsign '{callsign}' at the START of your message. Do NOT repeat it at the end.
        2. USE REAL WEATHER data from the METAR above.
        3. Reply in the SAME LANGUAGE as the user (Chinese/English).
           - Chinese: "国航1024, 地面风310, 8节..."
           - English: "CCA1024, Wind 310 at 8 knots..."
        4. IF frequency = Ground/Clearance, give IFR clearance if requested.
        5. Output JSON: {{"text": "...", "action": "NONE"}}
        6. READBACK HANDLING: If the pilot's readback is CORRECT, you do NOT need to say "Readback correct" every time. 
           - You may return an empty string "" for text to remain silent (simulate 'click' acknowledgment).
           - Or just reply with the callsign "{callsign}" to acknowledge.
           - ONLY correct them if the readback is WRONG.
        
        User said: "{user_input}"
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

    def generate_response(self, user_text=None, trigger_prompt=None, is_proactive=False, history=[]):
        if not self.client and not self.openai_client:
            print("LLMClient Error: Client not initialized.")
            return

        if is_proactive and trigger_prompt:
            system_prompt = trigger_prompt
        else:
            system_prompt = self._build_system_prompt(user_text, history=history)
            
        print("--- Generated System Prompt ---")
        print(system_prompt)
        print("-----------------------------")
        
        print(f"LLMClient: Sending request to {self.model}...")
        
        try:
            if self.client:
                # Conditional config based on model
                gen_config_args = {}
                
                # Google GenAI's Gemma models currently don't support JSON mode enforcement
                if "gemma" not in self.model.lower():
                    gen_config_args["response_mime_type"] = "application/json"
                else:
                    print(f"LLMClient: Model '{self.model}' detected. Disabling strict JSON mode enforcement.")

                response = self.client.models.generate_content(
                    model=self.model,
                    contents=system_prompt,
                    config=types.GenerateContentConfig(**gen_config_args)
                )
                response_text = response.text
                
            elif self.openai_client:
                # OpenAI / Compatible API Call
                response = self.openai_client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt}
                    ],
                    response_format={"type": "json_object"} if "json" in self.model.lower() or "gpt" in self.model.lower() else None
                )
                response_text = response.choices[0].message.content

            print(f"LLM Raw Response: {response_text}")
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

        print(f"LLMClient: Emitting response - Text: '{text[:50]}...', Action: {action}")
        self.bus.emit('llm_response_generated', text, action)