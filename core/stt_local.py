import os
import queue
import subprocess
import sys
import soundfile as sf
import tempfile
import multiprocessing
import threading
import time
import re

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

        # Whisper models don't support contextual biasing — hotwords only work
        # with transducer/RNN-T models and cause a C++ crash if passed here.
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


def _decode_audio_bytes(audio_data, recognizer):
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
        tmp.write(audio_data)
        tmp_path = tmp.name

    wav_path = tmp_path + ".wav"
    try:
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
            kwargs['creationflags'] = 0x08000000

        result = subprocess.run(
            [_ffmpeg, '-y', '-i', tmp_path, '-ar', '16000', '-ac', '1', wav_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            **kwargs
        )
        if result.returncode != 0:
            print(f"STTWorker: FFmpeg error: {result.stderr.decode(errors='replace')[:300]}")

        if not os.path.exists(wav_path):
            return None

        s = recognizer.create_stream()
        audio, sample_rate = sf.read(wav_path, dtype='float32')
        s.accept_waveform(sample_rate, audio)
        recognizer.decode_stream(s)
        return s.result.text.strip()
    finally:
        try:
            os.remove(wav_path)
        except Exception:
            pass
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def _persistent_sherpa_worker(language, encoder, decoder, tokens, request_queue, result_queue):
    try:
        import sherpa_onnx

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
        result_queue.put(("__ready__", True, "ready"))

        while True:
            item = request_queue.get()
            if item is None:
                break
            req_id, audio_data = item
            try:
                result_queue.put((req_id, True, _decode_audio_bytes(audio_data, recognizer)))
            except Exception as e:
                print(f"STTWorker decode error: {e}")
                result_queue.put((req_id, False, None))
    except Exception as e:
        print(f"STTWorker init error: {e}")
        result_queue.put(("__ready__", False, str(e)))


class STTLocal:
    def __init__(self, config, bus):
        self.config = config
        self.bus = bus
        # Search order:
        #   1. Next to the exe / sys._MEIPASS (compiled build — models shipped with installer)
        #   2. Next to this source file's project root (development)
        #   3. %APPDATA%\OpenFrequency\models (user-installed models)
        _appdata_models = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'OpenFrequency', 'models', 'sherpa-onnx-whisper-small')
        _exe_models = os.path.join(os.path.dirname(sys.executable), 'models', 'sherpa-onnx-whisper-small')
        _src_models = os.path.join(os.path.dirname(__file__), '..', 'models', 'sherpa-onnx-whisper-small')
        _meipass_models = os.path.join(getattr(sys, '_MEIPASS', ''), 'models', 'sherpa-onnx-whisper-small')
        for _candidate in (_meipass_models, _exe_models, _src_models, _appdata_models):
            if os.path.isdir(os.path.normpath(_candidate)):
                _default_models = os.path.normpath(_candidate)
                break
        else:
            _default_models = _appdata_models
        self.model_path = config.get('audio', {}).get('stt_model_path', _default_models)
        if self.model_path and not os.path.isdir(os.path.normpath(self.model_path)) and os.path.isdir(_appdata_models):
            self.model_path = _appdata_models
        self.current_language = config.get('audio', {}).get('stt_language', 'auto')
        self._hotwords: str = build_whisper_hotwords(config)
        self._model_files = {}
        self._request_queue = None
        self._result_queue = None
        self._worker_proc = None
        self._transcribe_lock = threading.Lock()

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
        _appdata_models = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'OpenFrequency', 'models', 'sherpa-onnx-whisper-small')
        if new_model and not os.path.isdir(os.path.normpath(new_model)) and os.path.isdir(_appdata_models):
            new_model = _appdata_models
        new_hw = build_whisper_hotwords(new_config)

        if new_lang != self.current_language or new_model != self.model_path:
            print(f"STTLocal: Language changed from '{self.current_language}' to '{new_lang}'. Reloading...")
            self.current_language = new_lang
            self.model_path = new_model
            self.config = new_config
            self._hotwords = new_hw
            self._resolve_model_files()
            self._stop_worker()
        elif new_hw != self._hotwords:
            self._hotwords = new_hw
            print(f"STTLocal: Hotwords updated (callsign/airline changed).")

    def _ensure_worker(self):
        if self._worker_proc is not None and self._worker_proc.is_alive():
            return True

        encoder = self._model_files.get('encoder')
        decoder = self._model_files.get('decoder')
        tokens = self._model_files.get('tokens')
        if not encoder or not os.path.exists(encoder):
            return False

        self._request_queue = multiprocessing.Queue()
        self._result_queue = multiprocessing.Queue()
        self._worker_proc = multiprocessing.Process(
            target=_persistent_sherpa_worker,
            args=(self.current_language, encoder, decoder, tokens, self._request_queue, self._result_queue),
            daemon=True,
        )
        print("STTLocal: Starting persistent Sherpa-ONNX worker...")
        self._worker_proc.start()
        try:
            req_id, ok, msg = self._result_queue.get(timeout=20)
            if req_id == "__ready__" and ok:
                print("STTLocal: Persistent worker ready.")
                return True
            print(f"STTLocal: Persistent worker failed to initialize: {msg}")
        except queue.Empty:
            print("STTLocal: Persistent worker initialization timed out.")

        self._stop_worker()
        return False

    def _stop_worker(self):
        proc = self._worker_proc
        if proc is None:
            return
        try:
            if proc.is_alive() and self._request_queue is not None:
                self._request_queue.put(None)
                proc.join(timeout=2)
            if proc.is_alive():
                proc.kill()
                proc.join()
        except Exception:
            pass
        self._worker_proc = None
        self._request_queue = None
        self._result_queue = None

    def _apply_context_bias(self, text):
        if not text:
            return text

        callsign = (self.config.get('user_profile', {}) or {}).get('callsign', '')
        if callsign:
            compact = re.sub(r'\s+', '', callsign).upper()
            if compact:
                spaced_pattern = r'\b' + r'\s*'.join(re.escape(ch) for ch in compact) + r'\b'
                text = re.sub(spaced_pattern, compact, text, flags=re.IGNORECASE)

        replacements = {
            r'\bgao\s*qi\b': 'Gaoqi',
            r'\bgao\s*chi\b': 'Gaoqi',
            r'\bxia\s*men\b': 'Xiamen',
            '高崎': 'Gaoqi',
            '厦门': 'Xiamen',
        }
        for pattern, replacement in replacements.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        return text

    def transcribe(self, audio_data):
        """
        Transcribes audio in an isolated subprocess so that a Sherpa-ONNX
        segfault cannot kill the main Flask process.
        """
        if not self._model_files.get('encoder') or not os.path.exists(self._model_files['encoder']):
            print("STTLocal: Model not available.")
            return

        print("STTLocal: Received audio data, dispatching to persistent inference worker...")

        with self._transcribe_lock:
            if not self._ensure_worker():
                return
            req_id = str(time.time_ns())
            self._request_queue.put((req_id, audio_data))
            text = None
            try:
                while True:
                    got_id, ok, payload = self._result_queue.get(timeout=30)
                    if got_id != req_id:
                        continue
                    if ok:
                        text = payload
                    break
            except queue.Empty:
                print("STTLocal: Inference timed out; restarting worker.")
                self._stop_worker()
                return

            if self._worker_proc is not None and self._worker_proc.exitcode not in (None, 0):
                print(f"STTLocal: Inference worker exited with code {self._worker_proc.exitcode}; restarting next request.")
                self._stop_worker()
                return

        if text:
            print(f"STTLocal: Raw transcription: '{text}'")
            corrected = correct_aviation_text(text)
            corrected = self._apply_context_bias(corrected)
            if corrected != text:
                print(f"STTLocal: Corrected → '{corrected}'")
            self.bus.emit('user_speech_recognized', corrected)
        else:
            print("STTLocal: No speech detected.")
