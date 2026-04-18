# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs


block_cipher = None
ROOT = Path(SPECPATH)
ICON = ROOT / "static" / "favicon.ico"


def collect_tree(relative_path, excludes=()):
    root = ROOT / relative_path
    if not root.exists():
        return []
    datas = []
    exclude_parts = {part.lower() for part in excludes}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        rel_parts = {part.lower() for part in rel.parts}
        if rel_parts & exclude_parts:
            continue
        normalized = "/".join(part.lower() for part in rel.parts)
        if relative_path == "ffmpeg":
            if normalized != "ffmpeg/bin/ffmpeg.exe":
                continue
        if "test_wavs" in rel_parts:
            continue
        if path.name.lower() in {"config.json", "llm_error.txt", "tmp_dashboard.js", "debug_tts.mp3"}:
            continue
        if path.suffix.lower() in {".pyc", ".pyo", ".tmp", ".log"} and "models" not in rel_parts:
            continue
        datas.append((str(path), str(rel.parent)))
    return datas


datas = []

# ── SimConnect SDK (PyPI package bundles SimConnect.dll) ────────────────────
# collect_all gathers: Python sources, data files (incl. the .dll), and
# compiled extensions so PyInstaller doesn't miss the native library.
_sc_datas, _sc_binaries, _sc_hiddenimports = [], [], []
try:
    _sc_datas, _sc_binaries, _sc_hiddenimports = collect_all('SimConnect')
except Exception:
    pass  # SimConnect not installed in this build env — optional dep
datas += _sc_datas

datas += collect_tree("templates")
datas += collect_tree("static", excludes={"cabin_media"})
# data/ includes quick_reply_templates.json, chatter_templates.json, etc.
# Exclude build-only artefacts and runtime-generated caches.
datas += collect_tree("data",    excludes={"reports", "storage", "ground_cache", "__pycache__"})
datas += collect_tree("models")
datas += collect_tree("ffmpeg")
datas += collect_tree("plugins", excludes={"__pycache__"})
# installer/ is build-tooling only — NOT bundled into the exe.

for filename in (
    "app.py",
    "config.example.json",
    "version.txt",
    "OpenFrequency-Icon.png",
    "README.md",
    "RELEASE_NOTES.md",
    "RELEASE_NOTES_zh-CN.md",
):
    path = ROOT / filename
    if path.exists():
        datas.append((str(path), "."))


# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------
# PyInstaller's static analysis misses:
#   • Modules imported inside try/except blocks (optional deps)
#   • Modules referenced only as strings (dynamic imports)
#   • Sub-packages whose __init__ is not imported at the top level
# ---------------------------------------------------------------------------

_hidden = [
    # ── Flask / SocketIO runtime ─────────────────────────────────────────────
    "app",
    "markdown",
    "engineio.async_drivers.threading",
    "gevent",

    # ── LLM backends ────────────────────────────────────────────────────────
    "google.genai",
    "google.genai.types",
    "openai",

    # ── Edge TTS (cloud) ─────────────────────────────────────────────────────
    "edge_tts",

    # ── Local TTS backends (optional — gracefully skipped if not installed) ──
    # Kokoro-ONNX: pip install kokoro-onnx
    "kokoro_onnx",
    # Piper TTS:   pip install piper-tts
    "piper",
    "piper.voice",

    # ── Audio / numeric processing used by local TTS ─────────────────────────
    "numpy",
    "numpy.core",
    "wave",
    "io",
    "soundfile",

    # ── STT (Sherpa-ONNX Whisper) ────────────────────────────────────────────
    "sherpa_onnx",

    # ── Simulator connectors ─────────────────────────────────────────────────
    "SimConnect",

    # ── Vision / head-tracking (optional) ───────────────────────────────────
    "cv2",
    "mediapipe",

    # ── System tray ──────────────────────────────────────────────────────────
    "pystray",
    "pystray._win32",

    # ── New core modules (loaded via EventBus; may not be auto-detected) ─────
    "core.quick_reply",       # quick-reply template engine
    "core.cpdlc_manager",     # CPDLC data-link session manager
    "core.china_airspace",    # China metric RVSM helpers

    # ── Plugin system (Round 8) ──────────────────────────────────────────────
    "core.plugin_api",        # OpenFrequencyPlugin base class
    "core.plugin_manager",    # manifest discovery + dynamic loading
    "core.addon_installer",   # DLC / FBW A32NX one-click installer

    # ── Dynamic import helpers used by plugin_manager ────────────────────────
    "importlib.util",
    "zipfile",
    "shutil",

    # ── Cloud services: telemetry / feedback / auto-update ───────────────────
    "core.telemetry",         # crash capture, sanitisation, KV upload
    "core.updater",           # version check, download, SHA-256 verify, install
    "core.feedback",          # manual feedback collection and submission
    # requests is already a dep of Flask ecosystem; ensure sub-packages included
    "requests.adapters",
    "requests.auth",
    "urllib3",
    "urllib3.util.retry",
]

a = Analysis(
    ["launcher.py"],
    pathex=[str(ROOT)],
    binaries=_sc_binaries,          # SimConnect.dll and any other native libs
    datas=datas,
    hiddenimports=_hidden + _sc_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # ── Dev / test tooling ───────────────────────────────────────────────
        "pytest", "unittest", "doctest",
        "IPython", "notebook", "jupyter",
        "tkinter", "_tkinter",

        # ── Deep learning / ML (not used at runtime) ────────────────────────
        # These can sneak in via transitive imports if installed in the env.
        # Explicitly excluding them keeps the output lean.
        "torch", "torchvision", "torchaudio",
        "paddle", "paddleocr", "paddlex", "paddlepaddle",
        "tensorflow", "keras",
        "sklearn", "scikit_learn",
        "transformers", "accelerate", "tokenizers", "datasets",
        "diffusers", "timm",
        "scipy",
        "xgboost", "lightgbm",
        "spacy", "nltk", "gensim",

        # ── Optional / heavy visualisation ──────────────────────────────────
        # Imported inside try/except in black_box.py — safe to exclude.
        "matplotlib", "pandas",

        # ── Unused UI toolkits ───────────────────────────────────────────────
        "pywebview", "wx", "PyQt5", "PyQt6", "PySide2", "PySide6",
        "pyautogui",           # only used in optional flight-report screenshot

        # ── Build-only artefacts ─────────────────────────────────────────────
        "installer",

        # ── Rarely-needed stdlib heavy modules ───────────────────────────────
        "test", "xmlrpc", "lib2to3",
        "ensurepip",
        # NOTE: do NOT exclude distutils — PyInstaller uses it internally
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OpenFrequency",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.exists() else None,
)
exe_console = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OpenFrequency-Console",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.exists() else None,
)
coll = COLLECT(
    exe,
    exe_console,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="OpenFrequency",
)
