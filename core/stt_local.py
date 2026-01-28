import sherpa_onnx
import os
import soundfile as sf
import tempfile
import time

class STTLocal:
    def __init__(self, config, bus):
        self.config = config
        self.bus = bus
        self.model_path = config.get('audio', {}).get('stt_model_path', './models/sherpa-onnx-whisper-small')
        
        print(f"STTLocal: Initializing Sherpa-ONNX Whisper...")
        print(f"STTLocal: Model Path: {self.model_path}")
        
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
            
            stt_lang = self.config.get('audio', {}).get('stt_language', 'en')
            
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
            # Create a temp file
            # We assume the client sends a format ffmpeg/soundfile can handle (e.g. valid wav/webm headers)
            # If webm, we might need ffmpeg to convert to wav for sherpa/soundfile?
            # Sherpa accepts wave filename, or samples.
            # Soundfile can read many formats.
            
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
                tmp.write(audio_data)
                tmp_path = tmp.name
            
            # Use soundfile to read it (SimConnect/PyAudio/etc might send raw PCM, but here it's likely webm from browser)
            # Sherpa needs float array + sample rate
            # BUT: soundfile might not support webm directly on Windows without external libs?
            # Let's try. If not, we might need a quick ffmpeg conversion.
            # Usually soundfile supports WAV/FLAC/OGG. WebM might be tricky.
            # If browser sends WebM (Opus), we definitely need conversion.
            
            # Let's use simple ffmpeg command to convert to wav if soundfile fails?
            # Or just use ffmpeg unconditionally to be safe.
            wav_path = tmp_path + ".wav"
            
            # Simple conversion using system ffmpeg (assumed in path)
            os.system(f'ffmpeg -y -i "{tmp_path}" -ar 16000 -ac 1 "{wav_path}" > nul 2>&1')
            
            if os.path.exists(wav_path):
                s = self.recognizer.create_stream()
                
                # Use soundfile to read the WAV
                # sherpa-onnx accept_waveform expects (sample_rate, samples)
                # samples should be float32
                audio, sample_rate = sf.read(wav_path, dtype='float32')
                
                s.accept_waveform(sample_rate, audio)
                
                self.recognizer.decode_stream(s)
                text = s.result.text.strip()
                
                print(f"STTLocal: Transcription result: '{text}'")
                if text:
                    self.bus.emit('user_speech_recognized', text)
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