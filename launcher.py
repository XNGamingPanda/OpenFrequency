from __future__ import annotations

import io
import json
import locale
import os
import runpy
import shutil
import signal
import socket
import sys
import threading
import time
import traceback
import wave
from datetime import datetime
from pathlib import Path

import requests
import sounddevice as sd
import webview


APP_NAME = "OpenFrequency"
DEFAULT_BIND_HOST = "0.0.0.0"
DEFAULT_UI_HOST = "127.0.0.1"
DEFAULT_PORT = "5000"

window_ref = None
tray_icon = None
desktop_lang = "en"
desktop_ptt_status_path = None


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


def migrate_external_config(config_path: Path):
    try:
        data = json.loads(config_path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return

    audio = data.setdefault("audio", {})
    if audio.get("stt_model_path") in {None, "", "base"}:
        audio["stt_model_path"] = "./models/sherpa-onnx-whisper-small"
        audio.setdefault("stt_language", "en")
    audio.setdefault("desktop_ptt_enabled", True)
    audio.setdefault("desktop_ptt_key", "space")
    config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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


def wait_for_server(host: str, port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def _resolve_bind_host(config_data: dict) -> str:
    env_host = (os.environ.get("OPENFREQUENCY_HOST") or "").strip()
    if env_host:
        return env_host

    network_cfg = config_data.get("network", {}) if isinstance(config_data, dict) else {}
    configured_host = str(network_cfg.get("host", "")).strip()
    if configured_host:
        return configured_host

    return DEFAULT_BIND_HOST


def _resolve_ui_host(bind_host: str) -> str:
    host = (bind_host or "").strip().lower()
    if host in {"", "0.0.0.0", "::", "[::]"}:
        return DEFAULT_UI_HOST
    return bind_host


def run_flask_app(root: Path):
    try:
        os.chdir(root)
        runpy.run_module("app", run_name="__main__")
    except Exception:
        traceback.print_exc()
        os._exit(1)


def force_exit():
    print("Launcher: Exiting OpenFrequency...")
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(0.5)
    os._exit(0)


class DesktopApi:
    def reload_window(self):
        if window_ref:
            window_ref.evaluate_js("window.location.reload()")
        return True

    def hide_window(self):
        if window_ref:
            window_ref.hide()
        return True

    def exit_app(self):
        threading.Thread(target=force_exit, daemon=True).start()
        return True


DESKTOP_I18N = {
    "en": {
        "refresh": "Refresh",
        "hide_to_tray": "Hide to Tray",
        "show_window": "Show Window",
        "refresh_window": "Refresh Window",
        "open_logs": "Open Logs",
        "exit_app": "Exit OpenFrequency",
    },
    "zh": {
        "refresh": "刷新",
        "hide_to_tray": "隐藏到托盘",
        "show_window": "显示窗口",
        "refresh_window": "刷新窗口",
        "open_logs": "打开日志",
        "exit_app": "退出 OpenFrequency",
    },
    "ja": {
        "refresh": "再読み込み",
        "hide_to_tray": "トレイに隠す",
        "show_window": "ウィンドウを表示",
        "refresh_window": "ウィンドウを再読み込み",
        "open_logs": "ログを開く",
        "exit_app": "OpenFrequency を終了",
    },
}


def desktop_text(key: str) -> str:
    return DESKTOP_I18N.get(desktop_lang, DESKTOP_I18N["en"]).get(key, key)


def detect_desktop_language(config_data: dict) -> str:
    configured = str(config_data.get("ui", {}).get("language", "")).strip().lower()
    if configured in {"en", "zh", "ja"}:
        return configured

    system_locale = (locale.getlocale()[0] or "").lower()
    if system_locale.startswith("zh"):
        return "zh"
    if system_locale.startswith("ja"):
        return "ja"
    return "en"


def inject_context_menu():
    if not window_ref:
        return
    js = """
    (() => {
      if (window.__ofDesktopContextMenuInstalled) return;
      window.__ofDesktopContextMenuInstalled = true;
      const menu = document.createElement('div');
      menu.id = 'of-desktop-menu';
      menu.style.cssText = 'position:fixed;display:none;z-index:2147483647;background:#111827;color:#fff;border:1px solid rgba(255,255,255,.12);border-radius:10px;box-shadow:0 12px 28px rgba(0,0,0,.35);padding:6px;min-width:160px;font:13px sans-serif;';
      const items = [
        {label:__REFRESH_LABEL__, action:() => window.pywebview.api.reload_window()},
        {label:__HIDE_LABEL__, action:() => window.pywebview.api.hide_window()}
      ];
      items.forEach(item => {
        const btn = document.createElement('button');
        btn.textContent = item.label;
        btn.style.cssText = 'display:block;width:100%;background:transparent;border:0;color:inherit;text-align:left;padding:8px 10px;border-radius:8px;cursor:pointer;';
        btn.onmouseenter = () => btn.style.background = 'rgba(255,255,255,.08)';
        btn.onmouseleave = () => btn.style.background = 'transparent';
        btn.onclick = () => { hideMenu(); item.action(); };
        menu.appendChild(btn);
      });
      document.body.appendChild(menu);
      function hideMenu(){ menu.style.display='none'; }
      document.addEventListener('click', hideMenu);
      document.addEventListener('contextmenu', (e) => {
        hideMenu();
        e.preventDefault();
        menu.style.left = `${e.clientX}px`;
        menu.style.top = `${e.clientY}px`;
        menu.style.display = 'block';
      });
      window.addEventListener('blur', hideMenu);
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hideMenu(); });
    })();
    """
    js = js.replace("__REFRESH_LABEL__", json.dumps(desktop_text("refresh")))
    js = js.replace("__HIDE_LABEL__", json.dumps(desktop_text("hide_to_tray")))
    try:
        window_ref.evaluate_js(js)
    except Exception as e:
        print(f"Launcher: Failed to inject desktop context menu: {e}")


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


def start_tray_icon(root: Path):
    global tray_icon
    try:
        import pystray

        image = _load_tray_image(root)
        if image is None:
            print("Tray icon disabled: failed to load or create tray image.")
            return

        def show_window(_icon=None, _item=None):
            if window_ref:
                window_ref.show()
                try:
                    window_ref.restore()
                except Exception:
                    pass

        def refresh_window(_icon=None, _item=None):
            if window_ref:
                window_ref.evaluate_js("window.location.reload()")

        def open_logs(_icon=None, _item=None):
            os.startfile(str(appdata_dir() / "logs"))

        def quit_app(icon=None, _item=None):
            if icon:
                icon.stop()
            threading.Thread(target=force_exit, daemon=True).start()

        tray_icon = pystray.Icon(
            APP_NAME,
            image,
            APP_NAME,
            menu=pystray.Menu(
                pystray.MenuItem(desktop_text("show_window"), show_window, default=True),
                pystray.MenuItem(desktop_text("refresh_window"), refresh_window),
                pystray.MenuItem(desktop_text("open_logs"), open_logs),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(desktop_text("exit_app"), quit_app),
            ),
        )
        threading.Thread(target=tray_icon.run, name="OpenFrequencyTray", daemon=True).start()
        print("Tray icon started.")
    except Exception:
        print("Tray icon disabled due to an initialization error:")
        traceback.print_exc()


class BackgroundPTTService:
    def __init__(self, server_base: str, enabled: bool = True, key_name: str = "space", ptt_binding: dict | None = None):
        self.server_base = server_base.rstrip("/")
        self.enabled = enabled
        self.key_name = (key_name or "space").lower()
        self.ptt_binding = ptt_binding or {}
        self.listener = None
        self.stream = None
        self.frames = []
        self.recording = False
        self.lock = threading.Lock()
        self.sample_rate = 16000
        self.channels = 1
        self.gamepad_thread = None
        self.gamepad_pressed = False
        self.last_upload_at = None
        self.last_error = ""
        self.matched_device = ""

    def _write_status(self, **updates):
        global desktop_ptt_status_path
        if not desktop_ptt_status_path:
            return
        payload = {
            "enabled": self.enabled,
            "key_name": self.key_name,
            "binding": self.ptt_binding,
            "recording": self.recording,
            "matched_device": self.matched_device,
            "last_upload_at": self.last_upload_at,
            "last_error": self.last_error,
            "updated_at": time.time(),
        }
        payload.update(updates)
        try:
            desktop_ptt_status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def start(self):
        if not self.enabled:
            print("Desktop PTT disabled by config.")
            self._write_status(enabled=False, last_error="")
            return
        try:
            from pynput import keyboard
        except Exception as e:
            print(f"Desktop PTT unavailable: pynput not installed ({e})")
            self.last_error = f"pynput not available: {e}"
            self._write_status(enabled=False, last_error=self.last_error)
            return

        def on_press(key):
            if self._matches(key):
                self._start_recording()

        def on_release(key):
            if self._matches(key):
                self._stop_recording()

        self.listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self.listener.daemon = True
        self.listener.start()
        print(f"Desktop PTT service started on key: {self.key_name}")
        self._write_status(enabled=True, last_error="")
        if self.ptt_binding.get("source") == "gamepad":
            self._start_gamepad_polling()

    def _matches(self, key) -> bool:
        try:
            from pynput import keyboard
            if self.key_name == "space":
                return key == keyboard.Key.space
            if self.key_name == "ctrl":
                return key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r)
            if self.key_name == "alt":
                return key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r)
            if self.key_name == "shift":
                return key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r)
            if hasattr(key, "char") and key.char:
                return key.char.lower() == self.key_name
        except Exception:
            return False
        return False

    def _start_recording(self):
        with self.lock:
            if self.recording:
                return
            self.frames = []
            self.recording = True
        self._write_status(recording=True, last_error="")
        try:
            requests.post(f"{self.server_base}/api/ptt_state", json={"active": True}, timeout=2)
        except Exception:
            pass

        def callback(indata, frames, _time_info, status):
            if status:
                print(f"Desktop PTT audio status: {status}")
            with self.lock:
                if self.recording:
                    self.frames.append(indata.copy())

        try:
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                callback=callback,
            )
            self.stream.start()
            print("Desktop PTT recording started.")
        except Exception as e:
            with self.lock:
                self.recording = False
            print(f"Desktop PTT failed to start recording: {e}")
            self.last_error = str(e)
            self._write_status(recording=False, last_error=self.last_error)

    def _stop_recording(self):
        with self.lock:
            if not self.recording:
                return
            self.recording = False
            frames = list(self.frames)
            self.frames = []
        self._write_status(recording=False)

        try:
            requests.post(f"{self.server_base}/api/ptt_state", json={"active": False}, timeout=2)
        except Exception:
            pass

        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        if not frames:
            return

        try:
            import numpy as np
            audio = np.concatenate(frames, axis=0)
            wav_bytes = self._to_wav_bytes(audio)
            requests.post(
                f"{self.server_base}/api/voice_data",
                data=wav_bytes,
                headers={"Content-Type": "audio/wav"},
                timeout=30,
            )
            print("Desktop PTT recording uploaded.")
            self.last_upload_at = time.time()
            self.last_error = ""
            self._write_status(last_upload_at=self.last_upload_at, last_error="")
        except Exception as e:
            print(f"Desktop PTT failed to upload recording: {e}")
            self.last_error = str(e)
            self._write_status(last_error=self.last_error)

    def _to_wav_bytes(self, audio):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio.tobytes())
        return buffer.getvalue()

    def _start_gamepad_polling(self):
        def run():
            try:
                import pygame
                pygame.init()
                pygame.joystick.init()
                print("Desktop PTT gamepad polling started.")
                while True:
                    pygame.event.pump()
                    gp_index = int(self.ptt_binding.get("gp", -1))
                    btn_index = int(self.ptt_binding.get("btn", -1))
                    target_name = str(self.ptt_binding.get("name", "")).strip()
                    if target_name:
                        for idx in range(pygame.joystick.get_count()):
                            js = pygame.joystick.Joystick(idx)
                            if not js.get_init():
                                js.init()
                            if js.get_name() == target_name:
                                gp_index = idx
                                self.matched_device = js.get_name()
                                break
                    if gp_index >= 0 and btn_index >= 0 and pygame.joystick.get_count() > gp_index:
                        joystick = pygame.joystick.Joystick(gp_index)
                        if not joystick.get_init():
                            joystick.init()
                        self.matched_device = joystick.get_name()
                        is_pressed = bool(joystick.get_button(btn_index))
                        if is_pressed and not self.gamepad_pressed:
                            self._start_recording()
                        elif not is_pressed and self.gamepad_pressed:
                            self._stop_recording()
                        self.gamepad_pressed = is_pressed
                        self._write_status(matched_device=self.matched_device, last_error="")
                    else:
                        self.matched_device = ""
                        self._write_status(matched_device="", last_error="")
                    time.sleep(0.02)
            except Exception as e:
                print(f"Desktop PTT gamepad polling unavailable: {e}")
                self.last_error = str(e)
                self._write_status(last_error=self.last_error)

        self.gamepad_thread = threading.Thread(target=run, name="DesktopPTTGamepad", daemon=True)
        self.gamepad_thread.start()


def on_window_closing():
    if window_ref:
        window_ref.hide()
    return False


def main():
    global window_ref, desktop_lang, desktop_ptt_status_path
    runtime_dir = appdata_dir()
    root = resource_root()
    setup_logging(runtime_dir)
    config_path = ensure_external_config(runtime_dir, root)
    desktop_ptt_status_path = runtime_dir / "desktop_ptt_status.json"

    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8") or "{}")
    except Exception:
        cfg = {}

    bind_host = _resolve_bind_host(cfg)
    ui_host = _resolve_ui_host(bind_host)
    port = int(os.environ.get("OPENFREQUENCY_PORT", DEFAULT_PORT))

    os.environ["OPENFREQUENCY_PACKAGED"] = "1"
    os.environ["OPENFREQUENCY_HOST"] = bind_host
    os.environ["OPENFREQUENCY_PORT"] = str(port)
    os.environ["OPENFREQUENCY_RUNTIME_DIR"] = str(runtime_dir)
    os.environ["OPENFREQUENCY_CONFIG_PATH"] = str(config_path)
    os.environ["OPENFREQUENCY_LOG_DIR"] = str(runtime_dir / "logs")
    os.environ["OPENFREQUENCY_DEBUG"] = "0"

    app_thread = threading.Thread(
        target=run_flask_app,
        args=(root,),
        name="OpenFrequencyFlask",
        daemon=True,
    )
    app_thread.start()

    print(f"Launcher: Waiting for Flask at http://{ui_host}:{port} ...")
    print(f"Launcher: Server bind address is http://{bind_host}:{port}")
    if not wait_for_server(ui_host, port):
        raise RuntimeError(f"Timed out waiting for OpenFrequency server at http://{ui_host}:{port}")

    desktop_lang = detect_desktop_language(cfg)
    start_tray_icon(root)
    audio_cfg = cfg.get("audio", {})
    ptt_service = BackgroundPTTService(
        server_base=f"http://{ui_host}:{port}",
        enabled=bool(audio_cfg.get("desktop_ptt_enabled", True)),
        key_name=audio_cfg.get("desktop_ptt_key", "space"),
        ptt_binding=audio_cfg.get("ptt_binding"),
    )
    ptt_service.start()

    url = f"http://{ui_host}:{port}/dashboard"
    print(f"Launcher: Opening pywebview window -> {url}")

    window_ref = webview.create_window(
        APP_NAME,
        url=url,
        width=1500,
        height=960,
        min_size=(1100, 720),
        text_select=True,
        js_api=DesktopApi(),
    )
    window_ref.events.closing += on_window_closing
    window_ref.events.loaded += inject_context_menu
    webview.start(debug=False)


if __name__ == "__main__":
    main()
