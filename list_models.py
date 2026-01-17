from google import genai
import json

def list_models():
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        api_key = config['connection']['api_key']
        client = genai.Client(api_key=api_key)
        
        print("Listing available models...")
        for m in client.models.list():
            print(f"- {m.name}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    list_models()
