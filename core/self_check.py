"""
Environment Self-Check Module.
Checks for required dependencies before starting the main application.
"""
import io
import os
import shutil
import tarfile
import urllib.request
import zipfile


MODEL_NAME = "sherpa-onnx-whisper-small"
MODEL_ARCHIVE = f"{MODEL_NAME}.tar.bz2"
MODEL_RELEASE_BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
MODEL_DOWNLOAD_URL = f"{MODEL_RELEASE_BASE}/{MODEL_ARCHIVE}"
MODEL_DOCS_URL = "https://k2-fsa.github.io/sherpa/onnx/pretrained_models/whisper/export-onnx.html"
MODEL_INSTALL_DIR = os.path.join(".", "models", MODEL_NAME)
MODEL_ARCHIVE_PATH = os.path.join(".", "models", MODEL_ARCHIVE)
MODEL_REQUIRED_FILES = [
    "small-tokens.txt",
    "small-encoder.int8.onnx",
    "small-decoder.int8.onnx",
]


def whisper_model_status():
    installed = os.path.isdir(MODEL_INSTALL_DIR) and all(
        os.path.exists(os.path.join(MODEL_INSTALL_DIR, filename))
        for filename in MODEL_REQUIRED_FILES
    )
    return {
        "installed": installed,
        "model_name": MODEL_NAME,
        "install_dir": MODEL_INSTALL_DIR,
        "archive_name": MODEL_ARCHIVE,
        "download_url": MODEL_DOWNLOAD_URL,
        "docs_url": MODEL_DOCS_URL,
        "required_files": list(MODEL_REQUIRED_FILES),
        "manual_steps": [
            f"Download {MODEL_ARCHIVE} from the official sherpa-onnx release page.",
            "Extract the archive.",
            f"Place the extracted folder at {MODEL_INSTALL_DIR}.",
            "Keep the int8 ONNX files and token file directly inside that folder.",
        ],
    }


def self_check():
    """
    Perform environment self-check.
    Returns: (success: bool, errors: list[dict])
    """
    errors = []

    ffmpeg_found = False
    local_ffmpeg = os.path.join(os.getcwd(), "ffmpeg", "bin", "ffmpeg.exe")

    if shutil.which("ffmpeg"):
        ffmpeg_found = True
    elif os.path.exists(local_ffmpeg):
        ffmpeg_found = True

    if not ffmpeg_found:
        errors.append({
            "id": "ffmpeg",
            "title": "FFmpeg Not Found",
            "message": "FFmpeg was not found. Download the bundled package or add FFmpeg to PATH.",
            "fixable": True,
        })

    model_info = whisper_model_status()
    if not model_info["installed"]:
        errors.append({
            "id": "whisper",
            "title": "AI Model Not Found",
            "message": "The sherpa-onnx Whisper STT model is missing. Download it from the official channel or install it manually into ./models.",
            "fixable": True,
        })

    config_path = os.environ.get("OPENFREQUENCY_CONFIG_PATH") or "config.json"
    if not os.path.exists(config_path):
        errors.append({
            "id": "config",
            "title": "Config File Missing",
            "message": "config.json was not found. The app will fall back to default configuration.",
            "fixable": False,
        })

    if errors:
        return False, errors

    return True, []


def download_ffmpeg():
    """
    Download portable FFmpeg for Windows.
    Returns: (success: bool, message: str)
    """
    try:
        print("Downloading FFmpeg...")
        url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"

        with urllib.request.urlopen(url, timeout=60) as response:
            zip_data = response.read()

        print("Extracting FFmpeg...")
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            for name in zf.namelist():
                if "bin/ffmpeg.exe" in name or "bin\\ffmpeg.exe" in name:
                    target_dir = os.path.join(os.getcwd(), "ffmpeg", "bin")
                    os.makedirs(target_dir, exist_ok=True)

                    for item in zf.namelist():
                        if item.replace("\\", "/").endswith("/bin/ffmpeg.exe"):
                            data = zf.read(item)
                            with open(os.path.join(target_dir, "ffmpeg.exe"), "wb") as handle:
                                handle.write(data)
                    break

        print("FFmpeg installed successfully.")
        return True, "FFmpeg installed successfully."

    except Exception as e:
        return False, f"FFmpeg download failed: {str(e)}"


def download_whisper_model():
    return download_whisper_model_with_progress()


def download_whisper_model_with_progress(progress_callback=None):
    """
    Download the official sherpa-onnx Whisper model and report progress.
    progress_callback receives (percent:int, message:str).
    """
    os.makedirs(os.path.dirname(MODEL_ARCHIVE_PATH), exist_ok=True)

    if whisper_model_status()["installed"]:
        if progress_callback:
            progress_callback(100, "Model already installed.")
        return True, "Model already installed."

    def update(percent, message):
        if progress_callback:
            progress_callback(max(0, min(100, int(percent))), message)

    def reporthook(block_count, block_size, total_size):
        if total_size <= 0:
            update(20, "Downloading model archive...")
            return
        downloaded = block_count * block_size
        ratio = min(downloaded / total_size, 1.0)
        update(10 + int(ratio * 60), "Downloading model archive...")

    try:
        update(5, "Preparing model download...")
        if os.path.exists(MODEL_ARCHIVE_PATH):
            os.remove(MODEL_ARCHIVE_PATH)

        urllib.request.urlretrieve(MODEL_DOWNLOAD_URL, MODEL_ARCHIVE_PATH, reporthook=reporthook)
        update(75, "Extracting model archive...")

        with tarfile.open(MODEL_ARCHIVE_PATH, "r:bz2") as archive:
            archive.extractall(path=os.path.join(".", "models"))

        update(95, "Verifying extracted files...")
        if not whisper_model_status()["installed"]:
            raise RuntimeError("Required model files were not found after extraction.")

        if os.path.exists(MODEL_ARCHIVE_PATH):
            os.remove(MODEL_ARCHIVE_PATH)

        update(100, "Model installed successfully.")
        return True, "Model installed successfully."
    except Exception as e:
        update(0, f"Model download failed: {e}")
        return False, f"Model download failed: {e}"
