# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path


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
        if "test_wavs" in rel_parts:
            continue
        if path.name.lower() in {"config.json", "llm_error.txt", "tmp_dashboard.js", "debug_tts.mp3"}:
            continue
        if path.suffix.lower() in {".pyc", ".pyo", ".tmp", ".log"} and "models" not in rel_parts:
            continue
        datas.append((str(path), str(rel.parent)))
    return datas


datas = []
datas += collect_tree("templates")
datas += collect_tree("static")
datas += collect_tree("data", excludes={"reports", "storage", "ground_cache", "__pycache__"})
datas += collect_tree("models")
datas += collect_tree("ffmpeg")
datas += collect_tree("plugins", excludes={"__pycache__"})

for filename in ("app.py", "config.example.json", "OpenFrequency-Icon.png", "README.md", "RELEASE_NOTES.md", "RELEASE_NOTES_zh-CN.md"):
    path = ROOT / filename
    if path.exists():
        datas.append((str(path), "."))


a = Analysis(
    ["desktop_launcher.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "app",
        "markdown",
        "engineio.async_drivers.threading",
        "gevent",
        "google.genai",
        "edge_tts",
        "sherpa_onnx",
        "soundfile",
        "cv2",
        "mediapipe",
        "SimConnect",
        "pystray",
        "pystray._win32",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "tkinter",
        "IPython",
        "notebook",
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
