import os
import subprocess
import sys
import soundfile as sf
import tempfile
import multiprocessing

from core.stt_post_processor import correct_aviation_text, build_whisper_hotwords


def _sherpa_worker(model_path, language, encoder, decoder, tokens, hotwords, audio_data, result_queue):
    """
    Runs entirely in a separate OS process. If Sherpa-ONNX segfaults,
    only this process dies — the parent Flask process is unaffected.
    """
    try:
        import sherpa_onnx

        # Convert audio bytes → WAV via ffmpeg
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp.write(audio_data)
            tmp_path = tmp.name

        wav_path = tmp_path + ".wav"

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
            print(f"STTWorker: FFmpeg error: {result.stderr.decode(errors='replace')[:300]}")

        if not os.path.exists(wav_path):
            result_queue.put(None)
            return

        stt_lang = language if language != 'auto' else ''

        recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=encoder,
            decoder=decoder,
            tokens=tokens,
            language=stt_lang,
            task="transcribe",
            num_threads=4,
            debug=False
        )

        try:
            s = recognizer.create_stream(hotwords=hotwords)
        except TypeError:
            s = recognizer.create_stream()

        audio, sample_rate = sf.read(wav_path, dtype='float32')
        s.accept_waveform(sample_rate, audio)
        recognizer.decode_stream(s)
        text = s.result.text.strip()

        result_queue.put(text)

    except Exception as e:
        print(f"STTWorker error: {e}")
        result_queue.put(None)
    finally:
        try:
            os.remove(wav_path)
        except Exception:
            pass
        try:
            os.remove(tmp_path)
        except Exception:
            pass


class STTLocal:
    def __init__(self, config, bus):
        self.config = config
        self.bus = bus
        _default_models = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'OpenFrequency', 'models', 'sherpa-onnx-whisper-small')
        self.model_path = config.get('audio', {}).get('stt_model_path', _default_models)
        self.current_language = config.get('audio', {}).get('stt_language', 'auto')
        self._hotwords: str = build_whisper_hotwords(config)
        self._model_files = {}

        print(f"STTLocal: Initializing Sherpa-ONNX Whisper (subprocess-isolated)...")
        print(f"STTLocal: Model Path: {self.model_path}")

        self._resolve_model_files()

        # Listen for config changes
        self.bus.on('config_updated', self._on_config_updated)

    def _resolve_model_files(self):
        tokens = os.path.join(self.model_path, "small-tokens.txt")
        encoder = os.path.join(self.model_path, "small-encoder.int8.onnx")
        decoder = os.path.join(self.model_path, "small-decoder.int8.onnx")

        if not os.path.exists(encoder):
            tokens = os.path.join(self.model_path, "tokens.txt")
            encoder = os.path.join(self.model_path, "encoder.int8.onnx")
            decoder = os.path.join(self.model_path, "decoder.int8.onnx")

        self._model_files = {"tokens": tokens, "encoder": encoder, "decoder": decoder}

        if os.path.exists(encoder):
            print("STTLocal: Model files resolved successfully.")
        else:
            print(f"STTLocal Error: Model files not found at {self.model_path}")

    def _on_config_updated(self, new_config):
        new_lang = new_config.get('audio', {}).get('stt_language', 'auto')
        new_model = new_config.get('audio', {}).get('stt_model_path', self.model_path)
        new_hw = build_whisper_hotwords(new_config)

        if new_lang != self.current_language or new_model != self.model_path:
            print(f"STTLocal: Language changed from '{self.current_language}' to '{new_lang}'. Reloading...")
            self.current_language = new_lang
            self.model_path = new_model
            self.config = new_config
            self._hotwords = new_hw
            self._resolve_model_files()
        elif new_hw != self._hotwords:
            self._hotwords = new_hw
            print(f"STTLocal: Hotwords updated (callsign/airline changed).")

    def transcribe(self, audio_data):
        """
        Transcribes audio in an isolated subprocess so that a Sherpa-ONNX
        segfault cannot kill the main Flask process.
        """
        if not self._model_files.get('encoder') or not os.path.exists(self._model_files['encoder']):
            print("STTLocal: Model not available.")
            return

        print("STTLocal: Received audio data, spawning inference subprocess...")

        result_queue = multiprocessing.Queue()
        proc = multiprocessing.Process(
            target=_sherpa_worker,
            args=(
                self.model_path,
                self.current_language,
                self._model_files['encoder'],
                self._model_files['decoder'],
                self._model_files['tokens'],
                self._hotwords,
                audio_data,
                result_queue,
            ),
            daemon=True,
        )
        proc.start()
        proc.join(timeout=30)  # Wait up to 30 s for inference

        if proc.is_alive():
            print("STTLocal: Inference subprocess timed out — killing.")
            proc.kill()
            proc.join()
            return

        if proc.exitcode != 0:
            print(f"STTLocal: Inference subprocess exited with code {proc.exitcode} (possible segfault — main process unaffected).")
            return

        text = result_queue.get_nowait() if not result_queue.empty() else None

        if text:
            print(f"STTLocal: Raw transcription: '{text}'")
            corrected = correct_aviation_text(text)
            if corrected != text:
                print(f"STTLocal: Corrected → '{corrected}'")
            self.bus.emit('user_speech_recognized', corrected)
        else:
            print("STTLocal: No speech detected.")
