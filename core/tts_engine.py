import asyncio
import base64
import edge_tts
from .context import event_bus, shared_context, context_lock

class TTSEngine:
    def __init__(self, config, socketio):
        self.config = config
        self.socketio = socketio
        event_bus.on('tts_request', self.speak)
        print("TTSEngine: Initialized and subscribed to 'tts_request'.")

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

    def _select_voice(self, icao_code):
        """
        Selects Edge-TTS voice based on ICAO code prefix.
        """
        if not icao_code or icao_code == 'N/A':
            return "en-US-ChristopherNeural"
        
        prefix = icao_code[0].upper()
        
        if prefix == 'Z':
            return "zh-CN-YunxiNeural"
        elif prefix == 'R':
            return "ja-JP-KeitaNeural"
        elif prefix in ['E', 'L', 'U']:
            return "en-GB-RyanNeural"
        else:
            return "en-US-ChristopherNeural"

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
        
        # Fallback: If NavManager isn't running (no DB), guess based on Lat/Lon
        if icao == 'N/A' and (lat != 0 or lon != 0):
            icao = self._guess_icao_prefix(lat, lon)
            print(f"TTSEngine: Guessed region '{icao}' based on Lat/Lon ({lat:.2f}, {lon:.2f})")
        
        voice = self._select_voice(icao)
        text_norm = self._normalize_text(text, voice)
        
        print(f"TTSEngine: Generating audio with voice '{voice}' -> '{text_norm}' (Orig: {text})")
        
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

    async def _generate(self, text, voice):
        # The actual edge-tts generation would happen here.
        # import edge_tts
        # communicate = edge_tts.Communicate(text, voice)
        # await communicate.save("output.mp3")
        pass