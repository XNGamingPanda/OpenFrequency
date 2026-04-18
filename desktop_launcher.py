"""
Desktop launcher for packaged OpenFrequency builds.

It starts the existing Flask/Socket.IO app in packaged mode, writes stdout and
stderr to a persistent log file, and opens the local dashboard in the user's
default browser. The console can be hidden by PyInstaller while logs remain
available under %APPDATA%\\OpenFrequency\\logs.
"""
from __future__ import annotations

import os
import runpy
import shutil
import signal
import sys
import threading
import time
import traceback
import webbrowser
import json
from datetime import datetime
from pathlib import Path


APP_NAME = "OpenFrequency"
DEFAULT_PORT = "5000"


class TeeStream:
    def __init__(self, *streams):
        self.streams = [stream for stream in streams if stream is not None]

    def write(self, data):
        for stream in self.streams:
            try:
                stream.write(data)
                stream.flush()
            except Exception:
                pass

    def flush(self):
        for stream in self.streams:
            try:
                stream.flush()
            except Exception:
                pass


def appdata_dir() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home())
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def setup_logging(runtime_dir: Path) -> Path:
    log_dir = runtime_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"openfrequency_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = TeeStream(getattr(sys, "stdout", None), log_file)
    sys.stderr = TeeStream(getattr(sys, "stderr", None), log_file)
    print(f"OpenFrequency desktop log: {log_path}")
    return log_path


def ensure_external_config(runtime_dir: Path, root: Path) -> Path:
    config_path = runtime_dir / "config.json"
    if config_path.exists():
        migrate_external_config(config_path)
        return config_path

    example = root / "config.example.json"
    if example.exists():
        shutil.copy2(example, config_path)
        print(f"Created default external config: {config_path}")
    else:
        config_path.write_text("{}", encoding="utf-8")
        print(f"Created empty external config: {config_path}")
    migrate_external_config(config_path)
    return config_path


def migrate_external_config(config_path: Path):
    try:
        data = json.loads(config_path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return

    audio = data.setdefault("audio", {})
    if audio.get("stt_model_path") in {None, "", "base"}:
        audio["stt_model_path"] = "./models/sherpa-onnx-whisper-small"
        audio.setdefault("stt_language", "en")
        config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Migrated STT model path in external config: {config_path}")


def open_browser_later(port: str):
    time.sleep(2.5)
    webbrowser.open(f"http://127.0.0.1:{port}/dashboard")


def _load_tray_image(root: Path):
    try:
        from PIL import Image, ImageDraw

        for candidate in (root / "static" / "favicon.ico", root / "OpenFrequency-Icon.png"):
            if candidate.exists():
                return Image.open(candidate)

        image = Image.new("RGBA", (64, 64), (18, 28, 40, 255))
        draw = ImageDraw.Draw(image)
        draw.ellipse((10, 10, 54, 54), fill=(20, 184, 166, 255))
        draw.text((24, 20), "O", fill=(255, 255, 255, 255))
        return image
    except Exception:
        traceback.print_exc()
        return None


def _exit_process():
    print("Tray exit requested. Shutting down OpenFrequency...")
    sys.stdout.flush()
    sys.stderr.flush()
    os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(0.5)
    os._exit(0)


def start_tray_icon(root: Path, port: str):
    try:
        import pystray

        image = _load_tray_image(root)
        if image is None:
            print("Tray icon disabled: failed to load or create tray image.")
            return

        def open_dashboard(_icon=None, _item=None):
            webbrowser.open(f"http://127.0.0.1:{port}/dashboard")

        def open_logs(_icon=None, _item=None):
            os.startfile(str(appdata_dir() / "logs"))

        def quit_app(icon=None, _item=None):
            if icon:
                icon.stop()
            threading.Thread(target=_exit_process, daemon=True).start()

        icon = pystray.Icon(
            APP_NAME,
            image,
            APP_NAME,
            menu=pystray.Menu(
                pystray.MenuItem("Open Dashboard", open_dashboard, default=True),
                pystray.MenuItem("Open Logs", open_logs),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit OpenFrequency", quit_app),
            ),
        )
        threading.Thread(target=icon.run, name="OpenFrequencyTray", daemon=True).start()
        print("Tray icon started.")
    except Exception:
        print("Tray icon disabled due to an initialization error:")
        traceback.print_exc()


def main():
    runtime_dir = appdata_dir()
    root = resource_root()
    setup_logging(runtime_dir)
    config_path = ensure_external_config(runtime_dir, root)

    # Install crash telemetry hooks as early as possible so any startup
    # exception is captured and written to the local crash log directory.
    os.environ.setdefault("OPENFREQUENCY_LOG_DIR", str(runtime_dir / "logs"))
    os.environ.setdefault("OPENFREQUENCY_CONFIG_PATH", str(config_path))
    try:
        from core import telemetry as _tel
        _tel.install_hooks()
        print("Telemetry: crash hooks installed.")
    except Exception as _e:
        print(f"Telemetry: failed to install hooks — {_e}")

    port = os.environ.get("OPENFREQUENCY_PORT", DEFAULT_PORT)
    os.environ.setdefault("OPENFREQUENCY_PACKAGED", "1")
    os.environ.setdefault("OPENFREQUENCY_HOST", "127.0.0.1")
    os.environ.setdefault("OPENFREQUENCY_PORT", port)
    os.environ.setdefault("OPENFREQUENCY_RUNTIME_DIR", str(runtime_dir))
    os.environ.setdefault("OPENFREQUENCY_CONFIG_PATH", str(config_path))
    os.environ.setdefault("OPENFREQUENCY_LOG_DIR", str(runtime_dir / "logs"))

    # In packaged mode the Flask app must not use the Werkzeug reloader.
    os.environ.setdefault("OPENFREQUENCY_DEBUG", "0")

    start_tray_icon(root, port)
    threading.Thread(target=open_browser_later, args=(port,), daemon=True).start()

    try:
        os.chdir(root)
        runpy.run_module("app", run_name="__main__")
    except Exception:
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
