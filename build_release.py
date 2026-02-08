import subprocess
import os

zip_name = "OpenFrequency_v2.5_Beta.zip"

def create_release_zip():
    # 7z exclusion list
    excludes = [
        '-x!config.json',
        '-x!debug_tts.mp3', 
        '-x!llm_error.txt',
        '-x!build_release.py',
        '-x!.gitignore',
        '-xr!.git',
        '-x!*.log',
        # Exclude directories
        '-xr!logs',
        '-xr!venv',
        '-xr!.venv',
        '-xr!__pycache__',
        '-xr!.vscode',
        '-xr!temp_audio',
        '-xr!brain',
        # Self exclusion
        f'-x!{zip_name}'
    ]

    # Try to find 7z if not in PATH
    seven_zip_cmd = '7z'
    possible_paths = [
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe"
    ]
    
    # Check if 7z is in PATH
    import shutil
    if not shutil.which('7z'):
        found = False
        for path in possible_paths:
            if os.path.exists(path):
                seven_zip_cmd = path
                found = True
                print(f"Found 7-Zip at: {path}")
                break
        if not found:
            print("Warning: 7-Zip not found in PATH or standard locations.")
            print("Please add 7-Zip to PATH or edit this script with the full path.")

    # Command construction
    # 7z a -tzip archive.zip . [excludes]
    cmd = [seven_zip_cmd, 'a', '-tzip', zip_name, '.'] + excludes

    print(f"Executing: {' '.join(cmd)}")
    
    try:
        subprocess.run(cmd, check=True)
        print(f"\nSuccess! Created {zip_name} using 7z")
    except FileNotFoundError:
        print(f"Error: '{seven_zip_cmd}' command not found.")
    except subprocess.CalledProcessError as e:
        print(f"Error: 7z failed with exit code {e.returncode}")

if __name__ == "__main__":
    create_release_zip()
