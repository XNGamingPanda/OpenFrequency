from google import genai
from google.genai import types
import json

def test_generation():
    try:
        # Load config
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
            
        api_key = config['connection']['api_key']
        model = config['connection']['model']
        
        print(f"Testing model: {model}")
        
        client = genai.Client(api_key=api_key)
        
        system_prompt = "You are an ATC simulator. Reply with a JSON object containing 'text' and 'action'."
        user_text = "Radio check."
        
        print("Sending request...")
        
        # Exact same call structure as llm_client.py
        response = client.models.generate_content(
            model=model,
            contents=system_prompt + "\nUser: " + user_text,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        
        print(f"Response: {response.text}")
        
    except Exception as e:
        print(f"FULL ERROR TRACEBACK:")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_generation()
