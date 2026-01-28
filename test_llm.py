from google import genai
from google.genai import types
import json
import openai

def test_generation():
    try:
        # Load config
        with open('config.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
            
        conn_config = config.get('connection', {})
        provider = conn_config.get('provider', 'google_genai')
        api_key = conn_config.get('api_key', '')
        model = conn_config.get('model', 'gemini-3-flash-preview')
        base_url = conn_config.get('base_url', None)
        
        print(f"Testing model: {model} with provider: {provider}")
        
        system_prompt = "You are an ATC simulator. Reply with a JSON object containing 'text' and 'action'."
        user_text = "Radio check."
        
        if provider == 'google_genai':
            client = genai.Client(api_key=api_key)
            print("Sending request (Google GenAI)...")
            response = client.models.generate_content(
                model=model,
                contents=system_prompt + "\nUser: " + user_text,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            print(f"Response: {response.text}")
            
        elif provider in ['openai', 'openai_compatible']:
            client = openai.OpenAI(api_key=api_key, base_url=base_url)
            print("Sending request (OpenAI/Compatible)...")
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text}
                ],
                response_format={"type": "json_object"}
            )
            print(f"Response: {response.choices[0].message.content}")
        
    except Exception as e:
        print(f"FULL ERROR TRACEBACK:")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_generation()
