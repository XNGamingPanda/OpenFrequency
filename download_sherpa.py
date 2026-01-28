import os
import urllib.request
import tarfile

def main():
    # GitHub Release (asr-models tag)
    base_url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    model_name = "sherpa-onnx-whisper-small"
    tar_filename = f"{model_name}.tar.bz2"
    url = f"{base_url}{tar_filename}"
    
    output_dir = "./models"
    os.makedirs(output_dir, exist_ok=True)
    
    tar_path = os.path.join(output_dir, tar_filename)
    final_model_dir = os.path.join(output_dir, model_name)

    print(f"--- Downloading {model_name} ---")
    print(f"URL: {url}")
    print(f"Destination: {tar_path}")

    # Check if already exists
    if os.path.exists(final_model_dir):
        print("Model directory already exists. Skipping download.")
        return

    try:
        urllib.request.urlretrieve(url, tar_path)
        print("Download complete. Extracting...")
        
        with tarfile.open(tar_path, "r:bz2") as tar:
            tar.extractall(path=output_dir)
            
        print(f"Extracted to {output_dir}")
        
        # Cleanup tar
        os.remove(tar_path)
        print("Done.")
        
    except Exception as e:
        print(f"Error downloading/extracting: {e}")
        # Fallback to slower Huggingface if Github fails? 
        # For now let's hope Github releases works. 
        # Alternately, we can try to download individual files if tar fails, but tar is cleaner.

if __name__ == "__main__":
    main()
