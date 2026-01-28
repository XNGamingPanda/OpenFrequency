import json
import os
import markdown
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
from apscheduler.schedulers.background import BackgroundScheduler

# Core imports
from core.context import shared_context, context_lock, event_bus
from core.logic_manager import LogicManager
from core.sim_bridge import SimBridge
from core.nav_manager import NavManager
from core.stt_local import STTLocal
from core.llm_client import LLMClient
from core.tts_engine import TTSEngine
from core.audio_listener import AudioListener

app = Flask(__name__)
app.config['SECRET_KEY'] = 'opensky_secret_key'
socketio = SocketIO(app, cors_allowed_origins="*")

# --- Environment Setup ---
# Check for local ffmpeg
local_ffmpeg_bin = os.path.join(os.getcwd(), 'ffmpeg', 'bin')
if os.path.isdir(local_ffmpeg_bin):
    print(f"System: Detected local FFmpeg at {local_ffmpeg_bin}")
    os.environ["PATH"] = local_ffmpeg_bin + os.pathsep + os.environ["PATH"]
else:
    print("System: No local FFmpeg found, relying on system PATH.")

# Load config
CONFIG_PATH = 'config.json'
config = {}
def load_config():
    global config
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
    else:
        print("Warning: config.json not found, using defaults.")
    
    # Sync context with config
    with context_lock:
        shared_context['aircraft']['callsign'] = config.get('user_profile', {}).get('callsign', 'N/A')
    print(f"System: Callsign initialized to {shared_context['aircraft']['callsign']}")

load_config()

# --- Web Routes ---
@app.route('/')
def index():
    # 1. Manual Override
    view_mode = request.args.get('view')
    if view_mode == 'mobile':
        return render_template('mobile_cockpit.html')
    elif view_mode == 'desktop':
        return render_template('dashboard.html')

    # 2. Auto Detection
    user_agent = request.headers.get('User-Agent', '').lower()
    is_mobile = "mobile" in user_agent or "android" in user_agent or "iphone" in user_agent
    
    if is_mobile:
        print(f"Device detected as Mobile: {user_agent}")
        return render_template('mobile_cockpit.html')
    else:
        print(f"Device detected as Desktop: {user_agent}")
        return render_template('dashboard.html')

@app.route('/get_config')
def get_config_route():
    load_config() # Reload from disk
    # Return safe copy with masked API key
    import copy
    config_safe = copy.deepcopy(config)
    if 'connection' in config_safe and 'api_key' in config_safe['connection']:
        if config_safe['connection']['api_key'] and len(config_safe['connection']['api_key']) > 5:
             config_safe['connection']['api_key'] = "******"
    return jsonify(config_safe)

def update_recursive(d, u):
    for k, v in u.items():
        if isinstance(v, dict):
            d[k] = update_recursive(d.get(k, {}), v)
        else:
            d[k] = v
    return d

@app.route('/save_settings', methods=['POST'])
def save_settings():
    global config
    new_config = request.json
    
    # Security: If API key is the mask, don't update it
    if 'connection' in new_config and 'api_key' in new_config['connection']:
        if new_config['connection']['api_key'] == "******":
            print("Security: Ignoring masked API key update.")
            del new_config['connection']['api_key']
    
    # Recursively update the config
    config = update_recursive(config, new_config)
    
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    
    # Sync runtime context
    with context_lock:
        if 'user_profile' in config and 'callsign' in config['user_profile']:
            shared_context['aircraft']['callsign'] = config['user_profile']['callsign']
            print(f"System: Callsign updated to {shared_context['aircraft']['callsign']}")

    print("Settings saved.")
    event_bus.emit('config_updated', config)
    return jsonify({"status": "success"})

@app.route('/import_simbrief', methods=['POST'])
def import_simbrief():
    import requests
    username = request.json.get('username')
    if not username:
        return jsonify({"status": "error", "message": "Username is required"}), 400

    print(f"Fetching SimBrief OFP for {username}...")
    try:
        base_url = "https://www.simbrief.com/api/xml.fetcher.php"
        params = {"username": username, "json": 1}
        resp = requests.get(base_url, params=params, timeout=10)
        
        if resp.status_code != 200:
            print(f"SimBrief API Failed. Status: {resp.status_code}")
            print(f"Response Body: {resp.text}")
            return jsonify({"status": "error", "message": f"SimBrief API returned {resp.status_code}. Check terminal for details."}), 502
            
        data = resp.json()
        
        # Validating response
        if 'fetch' in data and data['fetch']['status'] != 'Success':
             return jsonify({"status": "error", "message": f"SimBrief Error: {data['fetch']['status']}"}), 400

        # Parsing data
        general = data.get('general', {})
        origin = data.get('origin', {}).get('icao_code', 'N/A')
        dest = data.get('destination', {}).get('icao_code', 'N/A')
        alt_icao = data.get('alternate', {}).get('icao_code', 'N/A')
        cruise_alt = general.get('initial_altitude', 0)
        route = general.get('route', 'N/A')
        flight_number = general.get('flight_number', 'N/A')
        airline = general.get('icao_airline', 'N/A')
        
        # Update Shared Context
        with context_lock:
            shared_context['flight_plan'] = {
                "origin": origin,
                "destination": dest,
                "alternate": alt_icao,
                "route": route,
                "cruise_alt": cruise_alt,
                "flight_number": f"{airline}{flight_number}"
            }
            # Auto-update callsign if user wants? For now just update context flight plan.
            # We can also update config if we want to save this username
            config['simbrief']['username'] = username
        
        # Save username to config implicitly
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        print(f"Flight Plan Imported: {origin} -> {dest} via {route}")
        return jsonify({
            "status": "success", 
            "data": shared_context['flight_plan']
        })

    except Exception as e:
        print(f"SimBrief Import Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# --- SocketIO Handlers ---
@socketio.on('connect')
def handle_connect():
    print('Client connected')
    socketio.emit('status_update', {'status': 'connected', 'msg': 'System Ready'})
    
    # Send history
    if 'logic_manager' in globals() and hasattr(logic_manager, 'message_history'):
        history = logic_manager.message_history
        for msg in history:
            socketio.emit('chat_log', msg, room=request.sid) # Send only to new client

@socketio.on('voice_data')
def handle_voice_data(blob):
    """
    Receives voice data from the client and sends it to STT.
    """
    # This is a placeholder for where you'd pass the blob to the STT module
    # The audio_listener is for server-side mic, so we trigger the event directly
    # In a real scenario, stt.transcribe would be called here.
    print("Received voice data from client.")
    stt_module.transcribe(blob)

@socketio.on('text_input')
def handle_text_input(text):
    """
    Receives text input from the client and treats it as recognized speech.
    """
    print(f"Received text input: {text}")
    event_bus.emit('user_speech_recognized', text)

@socketio.on('test_tts_trigger')
def handle_test_tts():
    print("Received Test TTS request.")
    event_bus.emit('tts_request', "Station calling, radio check, read you five by five.")


if __name__ == '__main__':
    print("--- Initializing OpenSky-ATC v2.0 ---")
    
    # 1. Initialize all core modules
    print("Initializing modules...")
    logic_manager = LogicManager(config, socketio)
    sim_bridge = SimBridge(config, shared_context, context_lock, event_bus)
    nav_manager = NavManager(config, shared_context, context_lock, event_bus)
    stt_module = STTLocal(config, event_bus)
    llm_client = LLMClient(config, shared_context, context_lock, event_bus)
    tts_engine = TTSEngine(config, socketio)
    # audio_listener = AudioListener(config, stt_module.transcribe) # For server-side mic
    
    # 2. Start all background threads
    print("Starting background services...")
    logic_manager.start()
    sim_bridge.start()
    nav_manager.start()
    # audio_listener.start()
    
    # 3. Start the Web Server
    print("Starting Web Server on http://0.0.0.0:5000")
    
    # 4. Initialize and start the scheduler
    scheduler = BackgroundScheduler()
    scheduler.start()
    
    # Pass scheduler to LogicManager
    logic_manager.set_scheduler(scheduler)

    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)