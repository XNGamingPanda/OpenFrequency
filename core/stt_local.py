from faster_whisper import WhisperModel

class STTLocal:
    def __init__(self, config, bus):
        self.config = config
        self.bus = bus
        self.model_path = config.get('audio', {}).get('stt_model_path', 'small')
        self.device = "cpu"
        self.compute_type = "int8"
        print(f"STTLocal: Initialized. Loading Whisper model ({self.model_path})...")
        self.model = WhisperModel(self.model_path, device=self.device, compute_type=self.compute_type)
        print("STTLocal: Model loaded.")

    def transcribe(self, audio_data):
        """
        Transcribes audio and emits an event with the result.
        audio_data: Bytes/Blob data from the client.
        """
        print("STTLocal: Received audio data, saving to temp file...")
        
        # Save received blob to a temporary file
        # We assume the client sends a format ffmpeg can handle (e.g. webm/wav)
        import tempfile
        import os
        
        try:
            # Create a temp file
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
                tmp.write(audio_data)
                tmp_path = tmp.name
            
            print(f"STTLocal: Transcribing {tmp_path}...")
            
            # Run inference
            segments, info = self.model.transcribe(tmp_path, beam_size=5)
            text = "".join([s.text for s in segments]).strip()
            
            print(f"STTLocal: Transcription result: '{text}'")
            
            # Clean up
            os.remove(tmp_path)
            
            if text:
                self.bus.emit('user_speech_recognized', text)
            else:
                print("STTLocal: No speech detected.")
                
        except Exception as e:
            print(f"STTLocal Error: {e}")