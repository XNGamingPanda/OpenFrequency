import asyncio
import base64
import edge_tts
import hashlib
import threading
import queue
import time
from .context import event_bus, shared_context, context_lock

class TTSEngine:
    # Voice pools per region - each controller type will get a different voice
    VOICE_POOLS = {
        'Z': [  # China
            "zh-CN-YunxiNeural",      # Male
            "zh-CN-YunjianNeural",    # Male 2
            "zh-CN-XiaoyiNeural",     # Female
            "zh-CN-YunyangNeural",    # Male 3
        ],
        'R': [  # Japan
            "ja-JP-KeitaNeural",      # Male
            "ja-JP-NanamiNeural",     # Female
        ],
        'E': [  # Europe
            "en-GB-RyanNeural",       # British Male
            "en-GB-SoniaNeural",      # British Female
            "de-DE-ConradNeural",     # German Male
            "fr-FR-HenriNeural",      # French Male
        ],
        'K': [  # USA
            "en-US-ChristopherNeural",  # Male
            "en-US-GuyNeural",          # Male 2
            "en-US-JennyNeural",        # Female
            "en-US-EricNeural",         # Male 3
        ],
        'default': [  # Fallback
            "en-US-ChristopherNeural",
            "en-US-GuyNeural",
            "en-US-JennyNeural",
        ]
    }
    
    # AI Pilot voice pool - diverse accents for background chatter
    AI_PILOT_VOICES = [
        "en-GB-RyanNeural",       # British Male
        "en-US-GuyNeural",        # American Male
        "en-AU-WilliamNeural",    # Australian Male
        "en-IN-PrabhatNeural",    # Indian Male
        "en-GB-ThomasNeural",     # British Male 2
        "en-US-ChristopherNeural", # American Male 2
        "en-AU-NatashaNeural",    # Australian Female
        "en-GB-SoniaNeural",      # British Female
        "en-IE-ConnorNeural",     # Irish Male
        "en-NZ-MitchellNeural",   # New Zealand Male
    ]
    
    def __init__(self, config, socketio):
        self.config = config
        self.socketio = socketio
        
        # Audio queue with priority (lower number = higher priority)
        # Priority 1: Player/ATC conversation (critical)
        # Priority 2: Traffic alerts
        # Priority 3: Background chatter
        self.audio_queue = queue.PriorityQueue()
        self.is_playing = False
        self.ducking_active = False  # When True, suppress background audio
        self._queue_counter = 0  # For stable priority ordering
        
        # Subscribe to events
        event_bus.on('tts_request', self.speak)
        event_bus.on('chatter_tts_request', self._handle_chatter_request)
        event_bus.on('ptt_active', self._on_ptt_active)
        event_bus.on('ptt_released', self._on_ptt_released)
        
        self.runtime_voice_override = None
        print("TTSEngine: Initialized with chatter support.")

    def set_voice_override(self, voice_id):
        """Sets a specific voice to use for all ATC, overriding region logic."""
        if voice_id == "Auto" or not voice_id:
            self.runtime_voice_override = None
            print("TTSEngine: Voice override cleared (Auto).")
        else:
            self.runtime_voice_override = voice_id
            print(f"TTSEngine: Voice override set to '{voice_id}'.")

    def _guess_icao_prefix(self, lat, lon):
        """
        Rough geographical guessing for ICAO prefix if NavData is missing.
        """
        # China (Z)
        if 18 <= lat <= 54 and 73 <= lon <= 135:
            return 'Z'
        # USA (K) - Very rough
        if 24 <= lat <= 50 and -125 <= lon <= -66:
            return 'K'
        # Europe (E, L, U)
        if 36 <= lat <= 70 and -10 <= lon <= 40:
            return 'E'
        # Japan (R)
        if 30 <= lat <= 46 and 128 <= lon <= 146:
            return 'R'
        
        return 'K' # Default to US English

    def _select_voice(self, icao_code, controller_name):
        """
        Selects Edge-TTS voice based on ICAO code prefix AND controller name.
        Different controllers get different voices from the same region's pool.
        """
        # 1. Runtime Override (Debug Kit)
        if self.runtime_voice_override:
            return self.runtime_voice_override

        # 2. Language-based forcing (stt_language=ja forces Japanese voices)
        stt_lang = self.config.get('audio', {}).get('stt_language', 'auto')
        if stt_lang == 'ja':
            # Japanese mode: All voices are Japanese
            pool = self.VOICE_POOLS['R']  # Japan voice pool
            if controller_name:
                hash_val = int(hashlib.md5(controller_name.encode()).hexdigest(), 16)
                voice_index = hash_val % len(pool)
            else:
                voice_index = 0
            return pool[voice_index]
        
        # 3. Config-based Accent override (Legacy/Static)
        accent_override = self.config.get('debug', {}).get('accent_override', 'Auto')
        if accent_override and accent_override != 'Auto':
            # Map override values to ICAO prefixes
            override_map = {
                'China': 'Z',
                'USA': 'K', 
                'Japan': 'R',
                'UK': 'E'
            }
            prefix = override_map.get(accent_override, 'K')
        elif not icao_code or icao_code == 'N/A':
            prefix = 'default'
        else:
            prefix = icao_code[0].upper()
        
        # Get voice pool for this region
        pool = self.VOICE_POOLS.get(prefix, self.VOICE_POOLS['default'])
        
        # Use controller name to deterministically pick a voice from the pool
        # This ensures the same controller always gets the same voice
        if controller_name:
            # Hash the controller name to get a consistent index
            hash_val = int(hashlib.md5(controller_name.encode()).hexdigest(), 16)
            voice_index = hash_val % len(pool)
        else:
            voice_index = 0
        
        selected_voice = pool[voice_index]
        return selected_voice

    def speak(self, text):
        print(f"TTSEngine.speak() called with text: '{text[:50]}...'")
        # Use a new event loop in a separate thread to avoid conflicts with Flask-SocketIO
        import threading
        def run_async():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.speak_async(text))
                loop.close()
            except Exception as e:
                print(f"TTSEngine Error in thread: {e}")
        
        thread = threading.Thread(target=run_async, daemon=True)
        thread.start()

    def _normalize_text(self, text, voice):
        """
        Normalize text for specific languages/voices to improve pronunciation.
        """
        import re
        
        if voice.startswith("zh-"):
            # ONLY apply Chinese aviation digit substitution if the text actually contains Chinese characters.
            # This prevents English responses (e.g. "Contact Tower 118.1") from becoming "Contact Tower Yao Yao Ba...".
            if not re.search(r'[\u4e00-\u9fff]', text):
                return text

            # Force aviation digits for Chinese
            # 1 -> Yao (幺), 2 -> Liang (两), 7 -> Guai (拐), 0 -> Dong (洞)
            translation_table = str.maketrans({
                '1': '幺',
                '2': '两',
                '7': '拐',
                '0': '洞'
            })
            return text.translate(translation_table)
            
        return text

    async def speak_async(self, text):
        with context_lock:
            icao = shared_context['environment'].get('nearest_airport', 'N/A')
            lat = shared_context['aircraft'].get('latitude', 0)
            lon = shared_context['aircraft'].get('longitude', 0)
            controller_name = shared_context['atc_state'].get('current_controller', 'ATC')
        
        # Fallback: If NavManager isn't running (no DB), guess based on Lat/Lon
        if icao == 'N/A' and (lat != 0 or lon != 0):
            icao = self._guess_icao_prefix(lat, lon)
            print(f"TTSEngine: Guessed region '{icao}' based on Lat/Lon ({lat:.2f}, {lon:.2f})")
        
        voice = self._select_voice(icao, controller_name)
        text_norm = self._normalize_text(text, voice)
        
        print(f"TTSEngine: [{controller_name}] Using voice '{voice}' -> '{text_norm[:30]}...'")
        
        try:
            communicate = edge_tts.Communicate(text_norm, voice)
            full_audio = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    full_audio += chunk["data"]
            
            if full_audio:
                # Debug: Write to file to verify generation
                try:
                    with open("debug_tts.mp3", "wb") as f:
                        f.write(full_audio)
                    print(f"TTSEngine: Debug file written to debug_tts.mp3 ({len(full_audio)} bytes)")
                except Exception as e:
                    print(f"TTSEngine Debug Error: {e}")

                self.socketio.emit('audio_stream', {
                    'data': base64.b64encode(full_audio).decode('utf-8')
                })
                print(f"TTSEngine: Sent full audio ({len(full_audio)} bytes) to client.")
            else:
                print("TTSEngine: Warning - No audio data generated.")
                
        except Exception as e:
            print(f"Error during TTS generation or streaming: {e}")
    
    # ========== Chatter/Background Audio Support ==========
    
    def _handle_chatter_request(self, data):
        """Handle background chatter TTS requests."""
        if self.ducking_active:
            # Player is speaking, skip background audio
            return
        
        text = data.get('text', '')
        voice = data.get('voice')  # Pre-assigned voice for this callsign
        is_atc = data.get('is_atc', False)
        
        if not text:
            return
        
        # If no specific voice and it's a pilot, pick from AI pool
        if not voice and not is_atc:
            # Use the text hash to pick a consistent voice
            voice = self._select_ai_pilot_voice(text)
        elif is_atc:
            # Use region-appropriate ATC voice
            with context_lock:
                icao = shared_context['environment'].get('nearest_airport', 'N/A')
                lat = shared_context['aircraft'].get('latitude', 0)
                lon = shared_context['aircraft'].get('longitude', 0)
            
            if icao == 'N/A' and (lat != 0 or lon != 0):
                icao = self._guess_icao_prefix(lat, lon)
            
            voice = self._select_voice(icao, 'Chatter_ATC')
        
        print(f"TTSEngine: [Chatter] Using voice '{voice}' -> '{text[:30]}...'")
        
        # Generate and send audio in background thread
        def run_chatter():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self._generate_chatter_audio(text, voice))
                loop.close()
            except Exception as e:
                print(f"TTSEngine Chatter Error: {e}")
        
        thread = threading.Thread(target=run_chatter, daemon=True)
        thread.start()
    
    async def _generate_chatter_audio(self, text, voice):
        """Generate and emit chatter audio."""
        # Check ducking again before generating
        if self.ducking_active:
            return
        
        try:
            text_norm = self._normalize_text(text, voice)
            communicate = edge_tts.Communicate(text_norm, voice)
            full_audio = b""
            
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    full_audio += chunk["data"]
            
            if full_audio and not self.ducking_active:
                # Emit as background audio (separate event for volume control)
                self.socketio.emit('chatter_audio', {
                    'data': base64.b64encode(full_audio).decode('utf-8')
                })
                print(f"TTSEngine: Sent chatter audio ({len(full_audio)} bytes)")
                
        except Exception as e:
            print(f"TTSEngine Chatter Generation Error: {e}")
    
    def _select_ai_pilot_voice(self, identifier: str) -> str:
        """Select a consistent voice for an AI pilot based on identifier hash."""
        hash_val = int(hashlib.md5(identifier.encode()).hexdigest(), 16)
        return self.AI_PILOT_VOICES[hash_val % len(self.AI_PILOT_VOICES)]
    
    def _on_ptt_active(self, data=None):
        """Called when player starts speaking (PTT pressed)."""
        self.ducking_active = True
        # Optionally emit event to pause/duck audio on client
        self.socketio.emit('duck_audio', {'active': True})
    
    def _on_ptt_released(self, data=None):
        """Called when player stops speaking (PTT released)."""
        self.ducking_active = False
        self.socketio.emit('duck_audio', {'active': False})