"""
Environment Self-Check Module
Checks for required dependencies before starting the main application.
"""
import os
import shutil


def self_check():
    """
    Perform environment self-check.
    Returns: (success: bool, error_message: str)
    """
    errors = []
    
    # 1. Check FFmpeg
    ffmpeg_found = False
    local_ffmpeg = os.path.join(os.getcwd(), 'ffmpeg', 'bin', 'ffmpeg.exe')
    
    if shutil.which("ffmpeg"):
        ffmpeg_found = True
    elif os.path.exists(local_ffmpeg):
        ffmpeg_found = True
    
    if not ffmpeg_found:
        errors.append({
            "id": "ffmpeg",
            "title": "FFmpeg Not Found",
            "message": "未找到 FFmpeg。请下载整合包或将 FFmpeg 添加到系统环境变量。",
            "fixable": True
        })
    
    # 2. Check Whisper Model
    model_paths = [
        "./models/faster-whisper-small",
        "./models/faster-whisper-base",
        "./models/whisper-small"
    ]
    
    model_found = any(os.path.exists(p) for p in model_paths)
    
    if not model_found:
        errors.append({
            "id": "whisper",
            "title": "AI Model Not Found",
            "message": "未找到 Whisper AI 语音识别模型。请运行 download_models.py 下载模型。",
            "fixable": True
        })
    
    # 3. Check config.json exists
    if not os.path.exists("config.json"):
        errors.append({
            "id": "config",
            "title": "Config File Missing",
            "message": "未找到配置文件 config.json。将使用默认配置。",
            "fixable": False  # Will be auto-created
        })
    
    if errors:
        return False, errors
    
    return True, []


def download_ffmpeg():
    """
    Download portable FFmpeg for Windows.
    Returns: (success: bool, message: str)
    """
    import urllib.request
    import zipfile
    import io
    
    try:
        print("Downloading FFmpeg...")
        # Use a reliable mirror for portable ffmpeg
        url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
        
        # Download
        with urllib.request.urlopen(url, timeout=60) as response:
            zip_data = response.read()
        
        # Extract
        print("Extracting FFmpeg...")
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            # Find the bin folder in the zip
            for name in zf.namelist():
                if 'bin/ffmpeg.exe' in name or 'bin\\ffmpeg.exe' in name:
                    # Extract to ./ffmpeg/bin/
                    target_dir = os.path.join(os.getcwd(), 'ffmpeg', 'bin')
                    os.makedirs(target_dir, exist_ok=True)
                    
                    # Extract just the exe files from bin folder
                    for item in zf.namelist():
                        if '/bin/' in item and item.endswith('.exe'):
                            data = zf.read(item)
                            filename = os.path.basename(item)
                            with open(os.path.join(target_dir, filename), 'wb') as f:
                                f.write(data)
                    break
        
        print("FFmpeg installed successfully!")
        return True, "FFmpeg 安装成功！请刷新页面。"
        
    except Exception as e:
        return False, f"FFmpeg 下载失败: {str(e)}"


def download_whisper_model():
    """
    Download Whisper model (placeholder - actual download via faster-whisper).
    """
    try:
        print("Downloading Whisper model...")
        # faster-whisper will auto-download on first use
        # Just create the models directory
        os.makedirs("./models", exist_ok=True)
        
        # Trigger download by importing
        try:
            from faster_whisper import WhisperModel
            model = WhisperModel("small", device="cpu", compute_type="int8", 
                               download_root="./models")
            del model
            return True, "Whisper 模型下载成功！请刷新页面。"
        except Exception as e:
            return False, f"Whisper 模型下载失败: {str(e)}"
            
    except Exception as e:
        return False, f"模型下载失败: {str(e)}"
