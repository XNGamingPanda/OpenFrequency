import sherpa_onnx
import os
import subprocess
import sys
import soundfile as sf
import tempfile
import time

from core.stt_post_processor import correct_aviation_text, build_whisper_hotwords

class STTLocal:
    def __init__(self, config, bus):
        self.config = config
        self.bus = bus
        _default_models = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'OpenFrequency', 'models', 'sherpa-onnx-whisper-small')
        self.model_path = config.get('audio', {}).get('stt_model_path', _default_models)
        self.current_language = config.get('audio', {}).get('stt_language', 'auto')
        self.recognizer = None
        self._hotwords: str = build_whisper_hotwords(config)
        
        print(f"STTLocal: Initializing Sherpa-ONNX Whisper...")
        print(f"STTLocal: Model Path: {self.model_path}")
        
        self._init_recognizer()
        
        # Listen for config changes
        self.bus.on('config_updated', self._on_config_updated)
    
    def _on_config_updated(self, new_config):
        """Reload recognizer if language setting changed."""
        new_lang = new_config.get('audio', {}).get('stt_language', 'auto')
        new_model = new_config.get('audio', {}).get('stt_model_path', self.model_path)
        new_hw = build_whisper_hotwords(new_config)

        if new_lang != self.current_language or new_model != self.model_path:
            print(f"STTLocal: Language changed from '{self.current_language}' to '{new_lang}'. Reloading...")
            self.current_language = new_lang
            self.model_path = new_model
            self.config = new_config
            self._hotwords = new_hw
            self._init_recognizer()
        elif new_hw != self._hotwords:
            # Callsign/airline changed — update hotwords without reloading model
            self._hotwords = new_hw
            print(f"STTLocal: Hotwords updated (callsign/airline changed).")
    
    def _init_recognizer(self):
        """Initialize or reinitialize the Sherpa recognizer."""
        try:
            tokens = os.path.join(self.model_path, "small-tokens.txt")
            encoder = os.path.join(self.model_path, "small-encoder.int8.onnx")
            decoder = os.path.join(self.model_path, "small-decoder.int8.onnx")
            
            # Check if files exist, fallback to other common names if specific ones fail
            if not os.path.exists(encoder):
                 # Try other variants
                 tokens = os.path.join(self.model_path, "tokens.txt")
                 encoder = os.path.join(self.model_path, "encoder.int8.onnx") 
                 decoder = os.path.join(self.model_path, "decoder.int8.onnx")
            
            # 'auto' means let Whisper detect language (pass empty string or None)
            stt_lang = self.current_language if self.current_language != 'auto' else ''
            
            print(f"STTLocal: Using language: '{stt_lang}' (auto={self.current_language == 'auto'})")
            
            # Use factory method from_whisper directly as per 1.12.23 API behavior
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
                encoder=encoder,
                decoder=decoder,
                tokens=tokens,
                language=stt_lang,
                task="transcribe",
                num_threads=4,
                debug=False
            )
            print("STTLocal: Sherpa-ONNX Recognizer loaded successfully.")
        except Exception as e:
            print(f"STTLocal Error: Failed to load Sherpa model: {e}")
            self.recognizer = None

    def transcribe(self, audio_data):
        """
        Transcribes audio and emits an event with the result.
        audio_data: Bytes/Blob data from the client.
        """
        if not self.recognizer:
            print("STTLocal: Recognizer not initialized.")
            return

        print("STTLocal: Received audio data, processing...")
        
        # Save received blob to a temporary file
        try:
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
                tmp.write(audio_data)
                tmp_path = tmp.name
            
            wav_path = tmp_path + ".wav"
            
            # Resolve ffmpeg: prefer bundled copy inside PyInstaller package
            _ffmpeg = 'ffmpeg'
            if getattr(sys, 'frozen', False):
                _bundled = os.path.join(sys._MEIPASS, 'ffmpeg', 'ffmpeg.exe')
                if os.path.exists(_bundled):
                    _ffmpeg = _bundled
                else:
                    _bundled2 = os.path.join(sys._MEIPASS, 'ffmpeg.exe')
                    if os.path.exists(_bundled2):
                        _ffmpeg = _bundled2

            kwargs = {}
            if sys.platform == 'win32':
                kwargs['creationflags'] = 0x08000000  # CREATE_NO_WINDOW
            result = subprocess.run(
                [_ffmpeg, '-y', '-i', tmp_path, '-ar', '16000', '-ac', '1', wav_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                **kwargs
            )
            if result.returncode != 0:
                print(f"STTLocal: FFmpeg error: {result.stderr.decode(errors='replace')[:300]}")
            
            if os.path.exists(wav_path):
                # Pass aviation hotwords to bias Whisper decoding
                try:
                    s = self.recognizer.create_stream(hotwords=self._hotwords)
                except TypeError:
                    s = self.recognizer.create_stream()

                # Use soundfile to read the WAV
                audio, sample_rate = sf.read(wav_path, dtype='float32')
                
                s.accept_waveform(sample_rate, audio)

                try:
                    self.recognizer.decode_stream(s)
                except Exception as infer_err:
                    print(f"STTLocal: Sherpa inference error: {infer_err}")
                    return
                text = s.result.text.strip()
                
                print(f"STTLocal: Raw transcription: '{text}'")
                if text:
                    corrected = correct_aviation_text(text)
                    if corrected != text:
                        print(f"STTLocal: Corrected → '{corrected}'")
                    self.bus.emit('user_speech_recognized', corrected)
                else:
                    print("STTLocal: No speech detected.")
                
                # Cleanup
                try:
                    os.remove(wav_path)
                except: pass
            else:
                print("STTLocal: FFmpeg conversion failed.")
            
            try:
                os.remove(tmp_path)
            except: pass
                
        except Exception as e:
            print(f"STTLocal Error: {e}")