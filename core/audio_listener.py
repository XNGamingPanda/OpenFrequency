try:
    import pyaudio
    import webrtcvad
    import numpy as np
    import threading
    import collections

    class AudioListener:
        def __init__(self, config, callback):
            self.config = config
            self.callback = callback
            self.running = False
            self._thread = None
            # VAD setup
            self.vad = webrtcvad.Vad(3) # Aggressiveness mode 3
            self.RATE = 16000
            self.CHUNK_DURATION_MS = 30  # 30ms chunks
            self.CHUNK_SAMPLES = int(self.RATE * self.CHUNK_DURATION_MS / 1000)
            self.CHUNK_BYTES = self.CHUNK_SAMPLES * 2 # 16-bit audio
            self.PADDING_MS = 300 # 300ms padding
            self.NUM_PADDING_CHUNKS = int(self.PADDING_MS / self.CHUNK_DURATION_MS)
            self.RING_BUFFER_SIZE = self.NUM_PADDING_CHUNKS

        def start(self):
            if not self.running:
                self.running = True
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
                print("AudioListener started.")

        def stop(self):
            self.running = False
            if self._thread:
                self._thread.join()
            print("AudioListener stopped.")

        def _run(self):
            ring_buffer = collections.deque(maxlen=self.RING_BUFFER_SIZE)
            triggered = False
            voiced_frames = []

            p = pyaudio.PyAudio()
            stream = p.open(format=pyaudio.paInt16,
                            channels=1,
                            rate=self.RATE,
                            input=True,
                            frames_per_buffer=self.CHUNK_SAMPLES)
            
            print("Listening for voice activity...")
            while self.running:
                chunk = stream.read(self.CHUNK_SAMPLES)
                is_speech = self.vad.is_speech(chunk, self.RATE)

                if not triggered:
                    ring_buffer.append((chunk, is_speech))
                    num_voiced = len([f for f, s in ring_buffer if s])
                    if num_voiced > 0.9 * self.RING_BUFFER_SIZE:
                        triggered = True
                        print("Voice activity detected, starting recording...")
                        voiced_frames.extend([f for f, s in ring_buffer])
                        ring_buffer.clear()
                else:
                    voiced_frames.append(chunk)
                    ring_buffer.append((chunk, is_speech))
                    num_unvoiced = len([f for f, s in ring_buffer if not s])
                    if num_unvoiced > 0.9 * self.RING_BUFFER_SIZE:
                        triggered = False
                        print("Voice activity ended.")
                        # Process the recording
                        full_audio_data = b''.join(voiced_frames)
                        self.callback(full_audio_data)
                        voiced_frames = []
                        ring_buffer.clear()

            stream.stop_stream()
            stream.close()
            p.terminate()

except ModuleNotFoundError:
    print("*"*50)
    print("WARNING: PyAudio or other audio library not found.")
    print("Audio input will be disabled.")
    print("Please install PyAudio and other dependencies from requirements.txt")
    print("*"*50)
    class AudioListener:
        def __init__(self, config, callback):
            pass
        def start(self):
            pass
        def stop(self):
            pass
