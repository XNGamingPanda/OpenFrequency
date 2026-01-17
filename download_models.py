from faster_whisper import download_model
import os

def main():
    print("--- Downloading Whisper Model (Small) ---")
    # Download 'small' model to local ./models/whisper-small directory
    output_dir = "./models/whisper-small"
    
    # Ensure directory exists
    os.makedirs(output_dir, exist_ok=True)
    
    print(f"Downloading to: {os.path.abspath(output_dir)}")
    try:
        model_path = download_model("small", output_dir=output_dir)
        print(f"Success! Model downloaded to: {model_path}")
        print("You can now verify 'config.json' points to this path.")
    except Exception as e:
        print(f"Error downloading model: {e}")

if __name__ == "__main__":
    main()
