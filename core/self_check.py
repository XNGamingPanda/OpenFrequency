"""
Environment Self-Check + Model Downloader

Download sources (with mirror fallback for China users):
  STT  — sherpa-onnx-whisper-small  (HuggingFace / ModelScope)
  TTS  — Kokoro-ONNX v0.19          (GitHub / HuggingFace)
"""
from __future__ import annotations
import os
import shutil
from typing import Callable


# ─────────────────────────────────────────────────────────────────────────────
# Model catalogue
# ─────────────────────────────────────────────────────────────────────────────

def _appdata_models_dir() -> str:
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'OpenFrequency', 'models')

STT_MODEL_DIR  = os.path.join(_appdata_models_dir(), 'sherpa-onnx-whisper-small')
KOKORO_DIR     = _appdata_models_dir()

STT_FILES = [
    {
        "name":  "small-encoder.int8.onnx",
        "urls": [
            "https://hf-mirror.com/csukuangfj/sherpa-onnx-whisper-small/resolve/main/small-encoder.int8.onnx",
            "https://huggingface.co/csukuangfj/sherpa-onnx-whisper-small/resolve/main/small-encoder.int8.onnx",
        ],
        "size_mb": 112,
    },
    {
        "name":  "small-decoder.int8.onnx",
        "urls": [
            "https://hf-mirror.com/csukuangfj/sherpa-onnx-whisper-small/resolve/main/small-decoder.int8.onnx",
            "https://huggingface.co/csukuangfj/sherpa-onnx-whisper-small/resolve/main/small-decoder.int8.onnx",
        ],
        "size_mb": 262,
    },
    {
        "name":  "small-tokens.txt",
        "urls": [
            "https://hf-mirror.com/csukuangfj/sherpa-onnx-whisper-small/resolve/main/small-tokens.txt",
            "https://huggingface.co/csukuangfj/sherpa-onnx-whisper-small/resolve/main/small-tokens.txt",
        ],
        "size_mb": 0.1,
    },
]

KOKORO_FILES = [
    {
        "name":  "kokoro-v1.0.onnx",
        "urls": [
            "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
        ],
        "size_mb": 310,
    },
    {
        "name":  "voices-v1.0.bin",
        "urls": [
            "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
        ],
        "size_mb": 10,
    },
]

# Human-readable manual instructions shown on failure
STT_MANUAL_INSTRUCTIONS = {
    "zh": (
        "自动下载失败。请手动下载以下文件并放入 <code>models/sherpa-onnx-whisper-small/</code> 目录：\n"
        "• small-encoder.int8.onnx（约 112 MB）\n"
        "• small-decoder.int8.onnx（约 262 MB）\n"
        "• small-tokens.txt\n\n"
        "下载地址（任选一个）：\n"
        "① 国内镜像：https://hf-mirror.com/csukuangfj/sherpa-onnx-whisper-small/tree/main\n"
        "② HuggingFace：https://huggingface.co/csukuangfj/sherpa-onnx-whisper-small/tree/main"
    ),
}

KOKORO_MANUAL_INSTRUCTIONS = {
    "zh": (
        "自动下载失败。请手动下载以下文件并放入 <code>models/</code> 目录：\n"
        "• kokoro-v1.0.onnx（约 310 MB）\n"
        "• voices-v1.0.bin（约 10 MB）\n\n"
        "下载地址：\n"
        "GitHub Release：https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0"
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Core streaming downloader
# ─────────────────────────────────────────────────────────────────────────────

def _download_file(
    urls: list[str],
    dest_path: str,
    progress_cb: Callable[[int, str], None],
    label: str = "",
) -> bool:
    """
    Try each URL in order. Stream to dest_path, calling progress_cb(0-100, msg).
    Returns True on success.
    """
    import urllib.request
    import ssl

    # Skip if already present and non-empty
    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 1024:
        progress_cb(100, f"✓ {label} 已存在，跳过下载")
        return True

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for i, url in enumerate(urls):
        mirror = "HF Mirror" if "hf-mirror" in url else ("HuggingFace" if "huggingface" in url else ("ModelScope" if "modelscope" in url else "GitHub"))
        progress_cb(0, f"正在从 {mirror} 下载 {label}…")
        try:
            tmp_path = dest_path + ".tmp"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "OpenFrequency/1.0"},
            )
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
                with open(tmp_path, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = min(99, int(downloaded / total * 100))
                            mb = downloaded / 1_048_576
                            progress_cb(pct, f"下载中 {label}：{mb:.1f} / {total/1_048_576:.1f} MB")
            os.replace(tmp_path, dest_path)
            progress_cb(100, f"✓ {label} 下载完成")
            return True
        except Exception as e:
            if os.path.exists(tmp_path := dest_path + ".tmp"):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            if i < len(urls) - 1:
                progress_cb(0, f"镜像 {mirror} 失败，切换备用源…（{e}）")
            else:
                progress_cb(-1, f"✗ {label} 下载失败：{e}")

    return False


# ─────────────────────────────────────────────────────────────────────────────
# Public download helpers
# ─────────────────────────────────────────────────────────────────────────────

def download_stt_model(
    progress_cb: Callable[[int, str], None] | None = None,
) -> tuple[bool, str]:
    """
    Download sherpa-onnx-whisper-small STT model.
    progress_cb(percent: int, message: str) — percent=-1 means error.
    Returns (success, user_message).
    """
    if progress_cb is None:
        progress_cb = lambda p, m: print(f"[STT {p:3d}%] {m}")

    os.makedirs(STT_MODEL_DIR, exist_ok=True)
    total = len(STT_FILES)

    for idx, spec in enumerate(STT_FILES):
        dest = os.path.join(STT_MODEL_DIR, spec["name"])
        base_pct = idx * 100 // total
        end_pct  = (idx + 1) * 100 // total

        def _cb(pct: int, msg: str, _b=base_pct, _e=end_pct):
            if pct < 0:
                progress_cb(-1, msg)
            else:
                progress_cb(_b + pct * (_e - _b) // 100, msg)

        ok = _download_file(spec["urls"], dest, _cb, spec["name"])
        if not ok:
            return False, STT_MANUAL_INSTRUCTIONS["zh"]

    return True, "STT 语音识别模型下载完成！重启应用后生效。"


def download_tts_model(
    progress_cb: Callable[[int, str], None] | None = None,
) -> tuple[bool, str]:
    """
    Download Kokoro-ONNX TTS model files.
    Returns (success, user_message).
    """
    if progress_cb is None:
        progress_cb = lambda p, m: print(f"[TTS {p:3d}%] {m}")

    os.makedirs(KOKORO_DIR, exist_ok=True)
    total = len(KOKORO_FILES)

    for idx, spec in enumerate(KOKORO_FILES):
        dest = os.path.join(KOKORO_DIR, spec["name"])
        base_pct = idx * 100 // total
        end_pct  = (idx + 1) * 100 // total

        def _cb(pct: int, msg: str, _b=base_pct, _e=end_pct):
            if pct < 0:
                progress_cb(-1, msg)
            else:
                progress_cb(_b + pct * (_e - _b) // 100, msg)

        ok = _download_file(spec["urls"], dest, _cb, spec["name"])
        if not ok:
            return False, KOKORO_MANUAL_INSTRUCTIONS["zh"]

    return True, "TTS 语音合成模型下载完成！重启应用后生效。"


# ─────────────────────────────────────────────────────────────────────────────
# Legacy shim (kept for backward compat with rescue_fix endpoint)
# ─────────────────────────────────────────────────────────────────────────────

def download_whisper_model():
    return download_stt_model()


def download_ffmpeg():
    """Download portable FFmpeg for Windows."""
    import urllib.request
    import zipfile
    import io
    import ssl

    try:
        print("Downloading FFmpeg...")
        url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(url, timeout=120, context=ctx) as response:
            zip_data = response.read()

        print("Extracting FFmpeg...")
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            for name in zf.namelist():
                if '/bin/' in name and name.endswith('.exe'):
                    target_dir = os.path.join(os.getcwd(), 'ffmpeg', 'bin')
                    os.makedirs(target_dir, exist_ok=True)
                    filename = os.path.basename(name)
                    with open(os.path.join(target_dir, filename), 'wb') as f:
                        f.write(zf.read(name))

        return True, "FFmpeg 安装成功！请刷新页面。"
    except Exception as e:
        return False, f"FFmpeg 下载失败: {str(e)}"


# ─────────────────────────────────────────────────────────────────────────────
# Self-check
# ─────────────────────────────────────────────────────────────────────────────

def _stt_model_present() -> bool:
    d = STT_MODEL_DIR
    return (
        os.path.exists(os.path.join(d, "small-encoder.int8.onnx")) or
        os.path.exists(os.path.join(d, "encoder.int8.onnx"))
    )


def _tts_model_present() -> bool:
    return (
        os.path.exists(os.path.join(KOKORO_DIR, "kokoro-v1.0.onnx"))
        and os.path.exists(os.path.join(KOKORO_DIR, "voices-v1.0.bin"))
    ) or (
        os.path.exists(os.path.join(KOKORO_DIR, "kokoro-v0_19.onnx"))
        and os.path.exists(os.path.join(KOKORO_DIR, "voices.bin"))
    )


def self_check():
    errors = []

    # FFmpeg
    local_ffmpeg = os.path.join(os.getcwd(), 'ffmpeg', 'bin', 'ffmpeg.exe')
    if not shutil.which("ffmpeg") and not os.path.exists(local_ffmpeg):
        errors.append({
            "id": "ffmpeg",
            "title": "FFmpeg 未找到",
            "message": "未找到 FFmpeg，音频处理功能将无法使用。",
            "fixable": True,
            "manual": None,
        })

    # STT model
    if not _stt_model_present():
        errors.append({
            "id": "stt",
            "title": "STT 语音识别模型未找到",
            "message": "未找到 Whisper 语音识别模型（sherpa-onnx-whisper-small）。",
            "fixable": True,
            "files": [f"{s['name']} (~{s['size_mb']:.0f} MB)" for s in STT_FILES],
            "manual": STT_MANUAL_INSTRUCTIONS["zh"],
        })

    # TTS model (optional — edge-tts works without it)
    # We only warn, not block startup
    if not _tts_model_present():
        errors.append({
            "id": "tts",
            "title": "本地 TTS 模型未找到（可选）",
            "message": "未找到 Kokoro 本地语音合成模型。将使用在线 Edge TTS（需要网络）。",
            "fixable": True,
            "files": [f"{s['name']} (~{s['size_mb']:.0f} MB)" for s in KOKORO_FILES],
            "manual": KOKORO_MANUAL_INSTRUCTIONS["zh"],
        })

    # Config
    config_path = os.environ.get("OPENFREQUENCY_CONFIG_PATH") or "config.json"
    if not os.path.exists(config_path):
        errors.append({
            "id": "config",
            "title": "配置文件缺失",
            "message": "未找到 config.json，将使用默认配置自动创建。",
            "fixable": False,
            "manual": None,
        })

    # Block on ffmpeg + stt only
    blocking = [e for e in errors if e["id"] in ("ffmpeg", "stt")]
    if blocking:
        return False, errors
    return True, errors
