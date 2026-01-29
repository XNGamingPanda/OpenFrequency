import json
import os
import markdown
import secrets
import threading
import time
from flask import Flask, render_template, request, jsonify, redirect, make_response
from flask_socketio import SocketIO, join_room, leave_room, emit
from apscheduler.schedulers.background import BackgroundScheduler
from threading import Lock

# Core imports
from core.context import shared_context, context_lock, event_bus
from core.logic_manager import LogicManager
from core.sim_bridge import SimBridge
from core.nav_manager import NavManager
from core.stt_local import STTLocal
from core.llm_client import LLMClient
from core.tts_engine import TTSEngine
from core.auth_manager import AuthManager
from core.traffic_manager import TrafficStateManager
from core.chatter_generator import ChatterGenerator
from flask import Flask, render_template, request, jsonify, redirect, make_response
from flask_socketio import SocketIO, join_room, leave_room, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'opensky_secret_key'
socketio = SocketIO(app, cors_allowed_origins="*")
# auth_manager will be initialized after config is loaded

# --- Environment Setup ---
# Check for local ffmpeg
local_ffmpeg_bin = os.path.join(os.getcwd(), 'ffmpeg', 'bin')
if os.path.isdir(local_ffmpeg_bin):
    print(f"System: Detected local FFmpeg at {local_ffmpeg_bin}")
    os.environ["PATH"] = local_ffmpeg_bin + os.pathsep + os.environ["PATH"]
else:
    print("System: No local FFmpeg found, relying on system PATH.")

# Load config
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
print(f"CONFIG_PATH resolved to: {CONFIG_PATH}")
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

# --- Auth Manager (uses config) ---
auth_manager = AuthManager(config, CONFIG_PATH)

# --- Middleware & Auth ---
@app.before_request
def check_access():
    # 1. Static resources always allowed
    if request.path.startswith('/static') or request.path.startswith('/socket.io'):
        return None
    
    client_ip = request.remote_addr
    
    # 2. Block banned IPs immediately (no waiting room, no entry)
    if auth_manager.is_banned(client_ip):
        return "Access Denied", 403
    
    # 3. Waiting room allowed for non-banned users
    if request.path == '/waiting_room':
        return None

    token = request.cookies.get('auth_token')
    status = auth_manager.check_access(client_ip, token)
    
    if token:
         print(f"Debug: Auth Check IP={client_ip} Token={token[:5]}... Status={status}", flush=True)
    else:
         print(f"Debug: Auth Check IP={client_ip} No Token. Status={status}", flush=True)
    
    if status == 'ALLOW_ADMIN':
        return None # Proceed (Admin)
    
    if status == 'ALLOW' or status == 'ALLOW_GUEST':
        return None # Proceed (Trusted)
        
    if status == 'BLOCK':
        return "Access Denied (Banned)", 403
        
    if status == 'WAIT':
        return redirect('/waiting_room')

@app.route('/waiting_room')
def waiting_room():
    return render_template('waiting_room.html')

# --- Web Routes ---
@app.route('/')
def index():
    # Get user permission level
    client_ip = request.remote_addr
    token = request.cookies.get('auth_token')
    perm = auth_manager.get_permission_level(client_ip, token)
    can_interact = perm in ['ADMIN', 'FULL']  # Can send voice/text
    
    # 1. Manual Override
    view_mode = request.args.get('view')
    if view_mode == 'mobile':
        return render_template('mobile_cockpit.html', can_interact=can_interact, permission=perm)
    elif view_mode == 'desktop':
        return render_template('dashboard.html', can_interact=can_interact, permission=perm)

    # 2. Auto Detection
    user_agent = request.headers.get('User-Agent', '').lower()
    is_mobile = "mobile" in user_agent or "android" in user_agent or "iphone" in user_agent
    
    if is_mobile:
        print(f"Device detected as Mobile: {user_agent}")
        return render_template('mobile_cockpit.html', can_interact=can_interact, permission=perm)
    else:
        print(f"Device detected as Desktop: {user_agent}")
        return render_template('dashboard.html', can_interact=can_interact, permission=perm)

@app.route('/get_my_permission')
def get_my_permission():
    """Returns current user's permission level."""
    client_ip = request.remote_addr
    token = request.cookies.get('auth_token')
    perm = auth_manager.get_permission_level(client_ip, token)
    can_interact = perm in ['ADMIN', 'FULL']
    return jsonify({"permission": perm, "can_interact": can_interact})

@app.route('/settings')
def settings_page():
    # Only ADMIN and FULL users can access settings
    client_ip = request.remote_addr
    token = request.cookies.get('auth_token')
    perm = auth_manager.get_permission_level(client_ip, token)
    
    if perm not in ['ADMIN', 'FULL']:
        return redirect('/')  # Redirect readonly users to dashboard
    
    return render_template('settings.html')

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
    
    print("save_settings: Request received", flush=True)
    
    # Permission check: Only ADMIN or TRUSTED can modify settings
    client_ip = request.remote_addr
    token = request.cookies.get('auth_token')
    perm = auth_manager.get_permission_level(client_ip, token)
    
    if perm not in ['ADMIN', 'FULL']:
        print(f"Security: READONLY user ({client_ip}) tried to modify settings - DENIED")
        return jsonify({"status": "error", "message": "Permission denied. Read-only users cannot modify settings."}), 403
    
    new_config = request.json
    print(f"save_settings: Received config: {new_config}", flush=True)
    
    # Security: If API key is the mask, don't update it
    if 'connection' in new_config and 'api_key' in new_config['connection']:
        if new_config['connection']['api_key'] == "******":
            print("Security: Ignoring masked API key update.")
            del new_config['connection']['api_key']
    
    # Recursively update the config
    config = update_recursive(config, new_config)
    
    print(f"save_settings: Writing to {CONFIG_PATH}...", flush=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print(f"save_settings: Written successfully.", flush=True)
    
    # Sync runtime context
    with context_lock:
        if 'user_profile' in config and 'callsign' in config['user_profile']:
            shared_context['aircraft']['callsign'] = config['user_profile']['callsign']
            print(f"System: Callsign updated to {shared_context['aircraft']['callsign']}")
            
    # Sync Security Mode
    if 'security' in config and 'mode' in config['security']:
         auth_manager.set_mode(config['security']['mode'])

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
@socketio.on('connect')
def handle_connect():
    client_ip = request.remote_addr
    token = request.cookies.get('auth_token')
    status = auth_manager.check_access(client_ip, token)
    
    # Register this session with the token for tracking
    if token:
        auth_manager.register_session(token, request.sid)
    
    if status == 'ALLOW_ADMIN':
        join_room('admin_room')
        print(f"SocketIO: Admin connected from {client_ip}")
        # Send current pending requests to Admin?
        # socketio.emit('pending_requests', auth_manager.pending_requests, room=request.sid)

    elif status == 'WAIT':
        # Guest in waiting room
        print(f"SocketIO: Guest waiting from {client_ip}")
        # Notify admins?
        pass # Waiting for explicit 'request_entry' event
        
    else:
        print(f"SocketIO: Client connected (Status: {status})")

    socketio.emit('status_update', {'status': 'connected', 'msg': 'System Ready'}, room=request.sid)
    
    # Sync SimConnect Status
    if 'sim_bridge' in globals():
        is_connected = sim_bridge.connected
        msg = 'Connected to Simulator' if is_connected else 'Searching for Simulator...'
        socketio.emit('sim_status', {'connected': is_connected, 'msg': msg}, room=request.sid)

    # Send history
    if 'logic_manager' in globals() and hasattr(logic_manager, 'message_history'):
        history = logic_manager.message_history
        for msg in history:
            socketio.emit('chat_log', msg, room=request.sid) # Send only to new client

@socketio.on('request_sim_status')
def handle_request_sim_status():
    if 'sim_bridge' in globals():
        is_connected = sim_bridge.connected
        msg = 'Connected to Simulator' if is_connected else 'Searching for Simulator...'
        socketio.emit('sim_status', {'connected': is_connected, 'msg': msg}, room=request.sid)

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
    print("Received Test TTS request.")
    event_bus.emit('tts_request', "Station calling, radio check, read you five by five.")

@app.route('/get_auth_status')
def get_auth_status_route():
    """Returns current security mode."""
    # Only Admin (localhost) can see this strictly speaking, 
    # but for settings page usage we assume access is already checked by middleware.
    return jsonify({
        "mode": auth_manager.data.get('mode', 'doorbell'),
        "banned_count": len(auth_manager.data.get('banned_ips', []))
    })

@app.route('/set_security_mode', methods=['POST'])
def set_security_mode_route():
    data = request.json
    mode = data.get('mode')
    if auth_manager.set_mode(mode):
        print(f"Auth: Security Mode changed to {mode}")
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 400

@app.route('/devices')
def device_manager_page():
    # Only allow Admin/Localhost
    if auth_manager.check_access(request.remote_addr, None) != 'ALLOW_ADMIN':
         return "Admin Access Only", 403
    return render_template('device_manager.html')

@app.route('/get_auth_data')
def get_auth_data_route():
    if auth_manager.check_access(request.remote_addr, None) != 'ALLOW_ADMIN':
         return jsonify({}), 403
    
    # Include both persistent and temp tokens
    data = {
        "mode": auth_manager.data.get('mode', 'doorbell'),
        "trusted_tokens": auth_manager.data.get('trusted_tokens', {}),
        "temp_tokens": auth_manager.temp_tokens,
        "banned_ips": auth_manager.data.get('banned_ips', [])
    }
    return jsonify(data)

@app.route('/auth_action', methods=['POST'])
def auth_action_route():
    if auth_manager.check_access(request.remote_addr, None) != 'ALLOW_ADMIN':
         return jsonify({"status": "forbidden"}), 403
         
    data = request.json
    action = data.get('action')
    
    if action == 'revoke':
        affected_sessions = auth_manager.revoke_token(data.get('token'))
        # Force logout all affected sessions
        for sid in affected_sessions:
            socketio.emit('force_logout', {'reason': 'access_revoked'}, room=sid)
            print(f"Auth: Force logout sent to session {sid}")
    elif action == 'unban':
        auth_manager.unban_ip(data.get('ip'))
    elif action == 'set_permission':
        token = data.get('token')
        permission = data.get('permission')  # 'full' or 'readonly'
        if auth_manager.update_token_permissions(token, permission):
            print(f"Auth: Updated token permissions to {permission}")
            # Force refresh for affected sessions
            affected_sessions = auth_manager.token_sessions.get(token, [])
            for sid in affected_sessions:
                socketio.emit('permission_changed', {'permission': permission}, room=sid)
                print(f"Auth: Permission change notification sent to {sid}")
        else:
            return jsonify({"status": "error", "message": "Invalid token or permission"}), 400
         
    return jsonify({"status": "success"})

@socketio.on('request_entry')
def handle_request_entry(data):
    """Guest asking for permission."""
    client_ip = request.remote_addr
    ua = data.get('ua', 'Unknown')
    sid = request.sid
    
    # Ignore banned IPs silently
    if auth_manager.is_banned(client_ip):
        return
    
    print(f"Auth: Request Entry from {client_ip} ({ua})")
    
    # Store in AuthManager runtime storage
    auth_manager.pending_requests[sid] = {
        'ip': client_ip,
        'ua': ua,
        'ts': time.time()
    }
    
    req_data = {
        'sid': sid,
        'ip': client_ip,
        'ua': ua,
        'device_name': ua.split('(')[1].split(')')[0] if '(' in ua else "Unknown Device"
    }
    
    # Notify Admin
    socketio.emit('join_request', req_data, room='admin_room')

@socketio.on('admin_decision')
def handle_admin_decision(data):
    """Admin Approved/Denied a request."""
    if auth_manager.check_access(request.remote_addr, None) != 'ALLOW_ADMIN':
        print("Auth: Non-admin tried to make decision!")
        return

    target_sid = data.get('sid')
    action = data.get('action') # 'allow_once', 'trust', 'block', 'deny'
    
    # Retrieve original request info
    pending = auth_manager.pending_requests.get(target_sid)
    if not pending:
        print(f"Auth: No pending request found for SID {target_sid}. Client may have disconnected.")
        # Try to proceed anyway if we just want to issue a token? 
        # But we can't send it if they are gone.
        # If they are still connected but not in pending (restart?), we default.
        client_ip = "Unknown-Or-Stale"
        client_ua = "Unknown"
    else:
        client_ip = pending['ip']
        client_ua = pending['ua']
        # Remove from pending
        del auth_manager.pending_requests[target_sid]

    print(f"Auth Decision: {action} for {target_sid} ({client_ip})", flush=True)

    if action == 'deny':
        # Ban IP and deny access
        if pending:
            auth_manager.ban_ip(client_ip)
        socketio.emit('access_denied', {}, room=target_sid)
        print(f"Auth: Access denied and IP {client_ip} banned", flush=True)
        
    elif action in ['allow_once', 'trust']:
        # Generate Token
        persistent = (action == 'trust')
        print(f"Auth: Creating token for {client_ip}...", flush=True)
        token = auth_manager.create_token(client_ip, client_ua, persistent=persistent)
        print(f"Auth: Token created: {token[:10]}...", flush=True)
        
        # Send to client
        socketio.emit('access_granted', {'token': token}, room=target_sid)
        print(f"Auth: Token sent to {target_sid} (Persistent={persistent})", flush=True)

    elif action == 'block':
        # Block the IP from the pending request
        if pending:
             auth_manager.ban_ip(client_ip)
        socketio.emit('access_denied', {}, room=target_sid)
        print(f"Auth: IP {client_ip} blocked", flush=True)
    else:
        print(f"Auth: Unknown action '{action}'", flush=True)

    # Notify admin room to refresh device list
    socketio.emit('auth_data_changed', {}, room='admin_room')


if __name__ == '__main__':
    print("--- Initializing OpenSky-ATC v2.0 ---")
    print(f"Debug: WERKZEUG_RUN_MAIN = {os.environ.get('WERKZEUG_RUN_MAIN')}")

    # 1. Initialize all core modules
    print("Initializing modules...")
    logic_manager = LogicManager(config, socketio)
    sim_bridge = SimBridge(config, shared_context, context_lock, event_bus)
    nav_manager = NavManager(config, shared_context, context_lock, event_bus)
    stt_module = STTLocal(config, event_bus)
    llm_client = LLMClient(config, shared_context, context_lock, event_bus)
    tts_engine = TTSEngine(config, socketio)
    traffic_manager = TrafficStateManager(config, sim_bridge)
    chatter_generator = ChatterGenerator(config, tts_engine)
    # audio_listener = AudioListener(config, stt_module.transcribe) # For server-side mic
    
    # 2. Start all background threads
    # CRITICAL: Only start services in the worker process (reloader child), not the parent.
    # Otherwise SimConnect will be initialized twice and fail/freeze.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        print("Starting background services (Worker Process)...")
        logic_manager.start()
        sim_bridge.start()
        nav_manager.start()
        traffic_manager.start()
        # audio_listener.start()

        # 4. Initialize and start the scheduler
        scheduler = BackgroundScheduler()
        scheduler.start()
        
        # Pass scheduler to LogicManager
        logic_manager.set_scheduler(scheduler)
    else:
        print("System: Parent process started. Waiting for reloader to spawn worker...")

    # 3. Start the Web Server
    print("Starting Web Server on http://0.0.0.0:5000")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)