import asyncio
import base64
import io
import struct
import edge_tts
import hashlib
import threading
import queue
import time
import re
from .context import event_bus, shared_context, context_lock

try:
    import alkana
except Exception:
    alkana = None

# ── Optional local TTS backends ───────────────────────────────────────────────
try:
    from kokoro_onnx import Kokoro as _Kokoro
    _KOKORO_AVAILABLE = True
except Exception:
    _KOKORO_AVAILABLE = False

try:
    from piper.voice import PiperVoice as _PiperVoice
    _PIPER_AVAILABLE = True
except Exception:
    _PIPER_AVAILABLE = False

class TTSEngine:
    ENGLISH_VOICE = "en-US-ChristopherNeural"
    JAPANESE_VOICE = "ja-JP-KeitaNeural"
    CHINESE_VOICE = "zh-CN-YunxiNeural"
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
        self._atis_epoch = 0  # Incremented on each new ATIS request; stale threads bail out

        # Subscribe to events
        event_bus.on('tts_request', self.speak)
        event_bus.on('atis_tts_request', self.speak_atis)
        event_bus.on('chatter_tts_request', self._handle_chatter_request)
        event_bus.on('ptt_active', self._on_ptt_active)
        event_bus.on('ptt_released', self._on_ptt_released)
        
        self.runtime_voice_override = None

        # ── Local TTS backend ─────────────────────────────────────────────────
        audio_cfg = config.get('audio', {})
        self.tts_backend = audio_cfg.get('tts_backend', 'edge')   # 'edge' | 'local'
        self._local_engine = None
        if self.tts_backend == 'local':
            self._init_local_backend(audio_cfg)

        # Re-init backend when config changes
        event_bus.on('config_updated', self._on_config_updated)

        print(f"TTSEngine: Initialized. Backend='{self.tts_backend}'.")

    # ── Local backend init ────────────────────────────────────────────────────

    def _init_local_backend(self, audio_cfg: dict):
        """Try to load the configured local TTS engine (Kokoro or Piper)."""
        engine_name = audio_cfg.get('tts_local_engine', 'kokoro').lower()
        model_path  = audio_cfg.get('tts_local_model', '')

        if engine_name == 'kokoro' and _KOKORO_AVAILABLE:
            try:
                import os as _os
                _models_dir = _os.path.join(_os.environ.get('APPDATA') or _os.path.expanduser('~'), 'OpenFrequency', 'models')
                _default_model = _os.path.join(_models_dir, 'kokoro-v1.0.onnx')
                _default_voices = _os.path.join(_models_dir, 'voices-v1.0.bin')
                if not _os.path.exists(_default_model):
                    _default_model = _os.path.join(_models_dir, 'kokoro-v0_19.onnx')
                if not _os.path.exists(_default_voices):
                    _default_voices = _os.path.join(_models_dir, 'voices.bin')
                voices_bin = audio_cfg.get('tts_local_voices') or _default_voices
                if model_path and not _os.path.exists(model_path):
                    model_path = _default_model
                if voices_bin and not _os.path.exists(voices_bin):
                    voices_bin = _default_voices
                self._local_engine = _Kokoro(model_path or _default_model, voices_bin)
                self._local_engine_name = 'kokoro'
                print(f"TTSEngine: Kokoro local TTS loaded from '{model_path}'.")
            except Exception as e:
                print(f"TTSEngine: Kokoro init failed — {e}. Falling back to Edge-TTS.")
                self.tts_backend = 'edge'

        elif engine_name == 'piper' and _PIPER_AVAILABLE:
            try:
                self._local_engine = _PiperVoice.load(model_path)
                self._local_engine_name = 'piper'
                print(f"TTSEngine: Piper local TTS loaded from '{model_path}'.")
            except Exception as e:
                print(f"TTSEngine: Piper init failed — {e}. Falling back to Edge-TTS.")
                self.tts_backend = 'edge'
        else:
            missing = engine_name if engine_name != 'kokoro' else 'kokoro_onnx'
            print(f"TTSEngine: Local engine '{engine_name}' not available "
                  f"(install `{missing}`). Falling back to Edge-TTS.")
            self.tts_backend = 'edge'

    def _on_config_updated(self, new_config: dict):
        """Hot-reload TTS backend when settings change."""
        audio_cfg = new_config.get('audio', {})
        new_backend = audio_cfg.get('tts_backend', 'edge')
        if new_backend != self.tts_backend:
            self.tts_backend = new_backend
            self._local_engine = None
            if new_backend == 'local':
                self._init_local_backend(audio_cfg)
            print(f"TTSEngine: Backend switched to '{self.tts_backend}'.")

    # ── Synthesis dispatch ────────────────────────────────────────────────────

    async def _synthesize_audio(self, text: str, voice: str) -> bytes:
        """
        Synthesise *text* and return the complete audio as MP3/WAV bytes.
        Dispatches to Edge-TTS or the local backend depending on config.
        """
        if self.tts_backend == 'local' and self._local_engine:
            return await self._synthesize_local(text, voice)
        return await self._synthesize_edge(text, voice)

    async def _synthesize_edge(self, text: str, voice: str) -> bytes:
        """Original Edge-TTS synthesis (full audio, one shot)."""
        communicate = edge_tts.Communicate(text, voice)
        full_audio = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                full_audio += chunk["data"]
        return full_audio

    async def _synthesize_local(self, text: str, voice: str) -> bytes:
        """
        Local TTS synthesis.  Returns raw audio bytes (WAV for Piper, PCM16
        for Kokoro converted to WAV).

        The synthesis runs in a threadpool executor to avoid blocking the
        event loop (both Kokoro and Piper are synchronous).
        """
        loop = asyncio.get_event_loop()

        if self._local_engine_name == 'kokoro':
            def _run():
                # Map Edge-TTS voice tags to Kokoro voice names (best effort)
                kokoro_voice = self._edge_voice_to_kokoro(voice)
                samples, sr = self._local_engine.create(
                    text, voice=kokoro_voice, speed=1.0, lang='en-us'
                )
                return self._pcm_to_wav(samples, sr)
            return await loop.run_in_executor(None, _run)

        elif self._local_engine_name == 'piper':
            def _run():
                buf = io.BytesIO()
                with io.BytesIO() as wav_io:
                    import wave
                    with wave.open(wav_io, 'wb') as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(22050)
                        for audio_bytes in self._local_engine.synthesize_stream_raw(text):
                            wf.writeframes(audio_bytes)
                    return wav_io.getvalue()
            return await loop.run_in_executor(None, _run)

        return b""

    async def _stream_synthesize(self, text: str, voice: str):
        """
        Streaming synthesis — yields audio chunks as they are produced so the
        browser can start playing before synthesis is complete.

        For Edge-TTS: yields chunks directly from the stream.
        For local TTS: splits text into sentences and yields one WAV per sentence.
        Emits 'audio_stream_chunk' socket events; final chunk carries {'final': True}.
        """
        if self.tts_backend == 'local' and self._local_engine:
            sentences = self._split_sentences(text)
            for i, sentence in enumerate(sentences):
                if not sentence.strip():
                    continue
                chunk_bytes = await self._synthesize_local(sentence, voice)
                if chunk_bytes:
                    self.socketio.emit('audio_stream_chunk', {
                        'data':  base64.b64encode(chunk_bytes).decode('utf-8'),
                        'final': (i == len(sentences) - 1),
                    })
        else:
            # Edge-TTS native streaming
            communicate = edge_tts.Communicate(text, voice)
            buffer = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buffer += chunk["data"]
                    # Emit in ~16 KB chunks to reduce latency
                    if len(buffer) >= 16_384:
                        self.socketio.emit('audio_stream_chunk', {
                            'data':  base64.b64encode(buffer).decode('utf-8'),
                            'final': False,
                        })
                        buffer = b""
            if buffer:
                self.socketio.emit('audio_stream_chunk', {
                    'data':  base64.b64encode(buffer).decode('utf-8'),
                    'final': True,
                })

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """Split text into sentences for progressive local TTS streaming."""
        parts = re.split(r'(?<=[.!?,;])\s+', text)
        return [p for p in parts if p.strip()]

    @staticmethod
    def _edge_voice_to_kokoro(voice: str) -> str:
        """
        Map Edge-TTS voice identifiers to Kokoro voice names.
        Kokoro v0.19 built-in voices: af_bella, af_sarah, am_adam, am_michael,
                                       bf_emma, bf_isabella, bm_george, bm_lewis
        """
        mapping = {
            'en-US-ChristopherNeural': 'am_michael',
            'en-US-GuyNeural':         'am_adam',
            'en-US-JennyNeural':       'af_sarah',
            'en-US-EricNeural':        'am_michael',
            'en-GB-RyanNeural':        'bm_george',
            'en-GB-SoniaNeural':       'bf_emma',
            'en-GB-ThomasNeural':      'bm_lewis',
            'zh-CN-YunxiNeural':       'am_michael',   # fallback; Kokoro is EN-only
            'zh-CN-YunyangNeural':     'am_adam',
            'ja-JP-KeitaNeural':       'am_michael',
        }
        return mapping.get(voice, 'am_michael')

    @staticmethod
    def _pcm_to_wav(samples, sample_rate: int) -> bytes:
        """Convert float32 PCM samples (numpy array) to a WAV byte string."""
        import numpy as np
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
        buf = io.BytesIO()
        import wave
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm.tobytes())
        return buf.getvalue()

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

    def _contains_japanese(self, text):
        return bool(re.search(r'[\u3040-\u30ff]', text or ''))

    def _contains_chinese(self, text):
        return bool(re.search(r'[\u4e00-\u9fff]', text or ''))

    def _katakanaize_text(self, text):
        if not text:
            return text

        def replace_word(match):
            word = match.group(0)
            upper_map = {
                'ATIS': 'エイティス',
                'TOWER': 'タワー',
                'GROUND': 'グラウンド',
                'APPROACH': 'アプローチ',
                'DEPARTURE': 'デパーチャー',
                'CENTER': 'センター',
                'CONTACT': 'コンタクト',
                'CLIMB': 'クライム',
                'DESCEND': 'ディセンド',
                'RUNWAY': 'ランウェイ',
                'WIND': 'ウインド',
                'VISIBILITY': 'ビジビリティ',
                'ALTIMETER': 'アルティメーター',
                'INFORMATION': 'インフォメーション',
            }
            mapped = upper_map.get(word.upper())
            if mapped:
                return mapped
            if alkana:
                converted = alkana.get_kana(word.lower())
                if converted:
                    return converted
            if word.isupper() and len(word) <= 6:
                letter_map = {
                    'A': 'エー', 'B': 'ビー', 'C': 'シー', 'D': 'ディー', 'E': 'イー',
                    'F': 'エフ', 'G': 'ジー', 'H': 'エイチ', 'I': 'アイ', 'J': 'ジェー',
                    'K': 'ケー', 'L': 'エル', 'M': 'エム', 'N': 'エヌ', 'O': 'オー',
                    'P': 'ピー', 'Q': 'キュー', 'R': 'アール', 'S': 'エス', 'T': 'ティー',
                    'U': 'ユー', 'V': 'ブイ', 'W': 'ダブリュー', 'X': 'エックス', 'Y': 'ワイ',
                    'Z': 'ズィー'
                }
                return ' '.join(letter_map.get(ch, ch) for ch in word)
            return word

        return re.sub(r'[A-Za-z][A-Za-z0-9/-]*', replace_word, text)

    def _expand_english_digits(self, text):
        if not text:
            return text
        digit_words = {
            '0': 'Zero',
            '1': 'One',
            '2': 'Two',
            '3': 'Tree',
            '4': 'Fower',
            '5': 'Fife',
            '6': 'Six',
            '7': 'Seven',
            '8': 'Eight',
            '9': 'Niner',
        }

        def number_to_aviation_words(number_text):
            if not number_text.isdigit():
                return number_text

            if len(number_text) == 4 and number_text.endswith('000'):
                lead = digit_words.get(number_text[0], number_text[0])
                return f'{lead} Thousand'

            if len(number_text) == 3 and number_text.endswith('00'):
                lead = digit_words.get(number_text[0], number_text[0])
                return f'{lead} Hundred'

            return ' '.join(digit_words.get(ch, ch) for ch in number_text)

        def repl(match):
            token = match.group(0)
            if ',' in token:
                parts = token.split(',')
                return ' Decimal '.join(number_to_aviation_words(part) for part in parts)
            if '.' in token:
                parts = token.split('.')
                return ' Decimal '.join(number_to_aviation_words(part) for part in parts)
            if '/' in token:
                return ' slash '.join(number_to_aviation_words(part) for part in token.split('/'))
            if '-' in token:
                return ' dash '.join(number_to_aviation_words(part) for part in token.split('-'))
            return number_to_aviation_words(token)

        return re.sub(r'\d(?:[\d,/-]*\d)?(?:\.\d+)?', repl, text)

    def _expand_chinese_digits_for_speech(self, text):
        if not text:
            return text

        # China ATIS local convention: keep display as "3000米/3600米",
        # but read transition altitude/level as "三千米/三千六米".
        text = re.sub(
            r'(\u8fc7\u6e21\u9ad8\u5ea6)\s*3000\u7c73',
            lambda m: m.group(1) + '\u4e09\u5343\u7c73',
            text,
        )
        text = re.sub(
            r'(\u8fc7\u6e21\u9ad8\u5ea6\u5c42)\s*3600\u7c73',
            lambda m: m.group(1) + '\u4e09\u5343\u516d\u7c73',
            text,
        )

        digit_map = {
            '0': '洞', '1': '幺', '2': '两', '3': '三', '4': '四',
            '5': '五', '6': '六', '7': '拐', '8': '八', '9': '九'
        }

        # Convert runway designators before digit expansion: 34R→34右, 28L→28左, 36C→36中
        text = re.sub(r'(\d{1,2})([Rr])(?=\b|[^\w]|$)', lambda m: m.group(1) + '右', text)
        text = re.sub(r'(\d{1,2})([Ll])(?=\b|[^\w]|$)', lambda m: m.group(1) + '左', text)
        text = re.sub(r'(\d{1,2})([Cc])(?=\b|[^\w]|$)', lambda m: m.group(1) + '中', text)

        def convert_number(match):
            token = match.group(0)
            if token.endswith('米') and token[:-1].isdigit():
                num = token[:-1]
                if len(num) == 4 and num.endswith('000'):
                    return f"{digit_map.get(num[0], num[0])}千米"
                return ''.join(digit_map.get(ch, ch) for ch in num) + '米'
            if token.endswith('英尺') and token[:-2].isdigit():
                num = token[:-2]
                if len(num) == 4 and num.endswith('000'):
                    return f"{digit_map.get(num[0], num[0])}千英尺"
                return ''.join(digit_map.get(ch, ch) for ch in num) + '英尺'
            if '/' in token and token.replace('/', '').isdigit():
                return ''.join(digit_map.get(ch, ch) for ch in token if ch != '/')
            if token.isdigit():
                if len(token) == 4 and token.endswith('000'):
                    return f"{digit_map.get(token[0], token[0])}千"
                return ''.join(digit_map.get(ch, ch) for ch in token)
            return token

        return re.sub(r'\d+米|\d+英尺|\d+/\d+|\d+', convert_number, text)

    def _resolve_voice_and_text(self, text, icao, controller_name, force_mode=None):
        if force_mode == 'zh':
            return self.CHINESE_VOICE, self._expand_chinese_digits_for_speech(self._normalize_text(text, self.CHINESE_VOICE))
        if force_mode == 'en':
            return self.ENGLISH_VOICE, self._expand_english_digits(text)
        if force_mode == 'ja':
            return self.JAPANESE_VOICE, self._katakanaize_text(text)

        stt_lang = self.config.get('audio', {}).get('stt_language', 'auto')
        if self._contains_chinese(text):
            return self.CHINESE_VOICE, self._expand_chinese_digits_for_speech(self._normalize_text(text, self.CHINESE_VOICE))
        if self._contains_japanese(text) or stt_lang == 'ja':
            return self.JAPANESE_VOICE, self._katakanaize_text(text)
        return self.ENGLISH_VOICE, self._expand_english_digits(text)

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

    def speak_atis(self, text, icao=None):
        print(f"TTSEngine.speak_atis() called for {icao or 'N/A'}")
        self._atis_epoch += 1
        my_epoch = self._atis_epoch

        def run_async():
            _played = False
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                _icao_up = (icao or '').upper()
                _is_chinese_airport = (
                    _icao_up.startswith('Z') or
                    _icao_up.startswith('VH') or
                    _icao_up.startswith('VM') or
                    _icao_up.startswith('RC')
                )
                if icao and _is_chinese_airport and '\n\n' in text:
                    english_text, chinese_text = text.split('\n\n', 1)
                    english_audio = loop.run_until_complete(self._synthesize_audio(english_text, self.ENGLISH_VOICE))
                    chinese_spoken = self._expand_chinese_digits_for_speech(
                        self._normalize_text(chinese_text.replace(' / ', ' 和 '), self.CHINESE_VOICE)
                    )
                    chinese_audio = loop.run_until_complete(
                        self._synthesize_audio(chinese_spoken, self.CHINESE_VOICE)
                    )
                    full_audio = english_audio + chinese_audio
                    # Bail out if a newer ATIS request arrived while we were synthesizing
                    if full_audio and self._atis_epoch == my_epoch:
                        # Estimate playback duration so atis_played fires after audio ends on client
                        duration_ms = max(1000, int(len(full_audio) / 16000 * 1000))
                        self.socketio.emit('audio_stream', {
                            'data': base64.b64encode(full_audio).decode('utf-8'),
                            'is_atis': True,
                            'icao': icao,
                            'duration_ms': duration_ms,
                        })
                        import time as _time
                        _time.sleep(duration_ms / 1000.0)
                else:
                    if self._atis_epoch == my_epoch:
                        loop.run_until_complete(self.speak_async(text, icao_override=icao))
                loop.close()
                if icao and self._atis_epoch == my_epoch:
                    event_bus.emit('atis_played', icao)
                    _played = True
            except Exception as e:
                print(f"TTSEngine ATIS Error in thread: {e}")
            finally:
                # Keep the loop alive even if synthesis failed
                if not _played and icao and self._atis_epoch == my_epoch:
                    import time as _time
                    _time.sleep(5)  # brief pause before retry
                    event_bus.emit('atis_played', icao)

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

    async def _synthesize_audio(self, text, voice):
        communicate = edge_tts.Communicate(text, voice)
        full_audio = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                full_audio += chunk["data"]
        return full_audio

    async def speak_async(self, text, force_mode=None, icao_override=None):
        with context_lock:
            icao = icao_override or shared_context['environment'].get('nearest_airport', 'N/A')
            lat = shared_context['aircraft'].get('latitude', 0)
            lon = shared_context['aircraft'].get('longitude', 0)
            controller_name = shared_context['atc_state'].get('current_controller', 'ATC')
        
        # Fallback: If NavManager isn't running (no DB), guess based on Lat/Lon
        if icao == 'N/A' and (lat != 0 or lon != 0):
            icao = self._guess_icao_prefix(lat, lon)
            print(f"TTSEngine: Guessed region '{icao}' based on Lat/Lon ({lat:.2f}, {lon:.2f})")

        voice, text_norm = self._resolve_voice_and_text(text, icao, controller_name, force_mode=force_mode)
        
        print(f"TTSEngine: [{controller_name}] Using voice '{voice}' -> '{text_norm[:30]}...'")
        
        try:
            if self.tts_backend == 'local' and self._local_engine:
                # Streaming path: emit chunks progressively for lower latency
                print(f"TTSEngine: [Local/{self._local_engine_name}] Streaming '{text_norm[:40]}...'")
                await self._stream_synthesize(text_norm, voice)
            else:
                # Edge-TTS: full audio then emit once
                full_audio = await self._synthesize_edge(text_norm, voice)
                if full_audio:
                    try:
                        with open("debug_tts.mp3", "wb") as f:
                            f.write(full_audio)
                    except Exception:
                        pass
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
        is_cabin = data.get('is_cabin', False)
        
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
                loop.run_until_complete(self._generate_chatter_audio(text, voice, is_cabin=is_cabin))
                loop.close()
            except Exception as e:
                print(f"TTSEngine Chatter Error: {e}")
        
        thread = threading.Thread(target=run_chatter, daemon=True)
        thread.start()
    
    async def _generate_chatter_audio(self, text, voice, is_cabin=False):
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
                    'data': base64.b64encode(full_audio).decode('utf-8'),
                    'is_cabin': is_cabin
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
