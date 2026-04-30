import multiprocessing
multiprocessing.freeze_support()  # Must be called early for PyInstaller + Windows spawn

import json
import os
import markdown
import secrets
import threading
import time
from flask import Flask, render_template, request, jsonify, redirect, make_response, Response, stream_with_context
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
from core.atis_generator import ATISGenerator
from core.airport_frequency_service import AirportFrequencyService
from core.ground_data_service import GroundDataService
from core.atc_monitor import ATCMonitor
from core.aircraft_catalog import AircraftCatalog
from core.self_check import self_check, download_ffmpeg, download_whisper_model, download_stt_model, download_tts_model
from core.career import CareerProfile  # Career Mode
from core.crew_manager import CrewManager  # Crew Manager (FO + Purser)
from core.plugin_manager import PluginManager
_plugin_manager = None  # set during startup; guards against pre-init requests
from core.addon_installer import get_installer, load_dlc_catalog, current_progress
from core import telemetry as _telemetry_mod
from core import stats as _stats_mod
from core import updater as _updater_mod
from core import feedback as _feedback_mod
from flask import Flask, render_template, request, jsonify, redirect, make_response
from flask_socketio import SocketIO, join_room, leave_room, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('OPENFREQUENCY_SECRET_KEY') or secrets.token_hex(32)

def _select_socketio_async_mode():
    preferred = os.environ.get('OPENFREQUENCY_SOCKETIO_ASYNC_MODE', 'threading').strip().lower()
    if preferred == 'gevent':
        try:
            import gevent  # noqa: F401
            print("System: Using Socket.IO async_mode='gevent'")
            return 'gevent'
        except Exception as e:
            print(f"System: gevent unavailable, falling back to threading - {e}")

    print("System: Using Socket.IO async_mode='threading'")
    return 'threading'

socketio = SocketIO(app, cors_allowed_origins="*", async_mode=_select_socketio_async_mode())
# auth_manager will be initialized after config is loaded
traffic_manager = None

# --- Environment Setup ---
# Check for local ffmpeg
local_ffmpeg_bin = os.path.join(os.getcwd(), 'ffmpeg', 'bin')
if os.path.isdir(local_ffmpeg_bin):
    print(f"System: Detected local FFmpeg at {local_ffmpeg_bin}")
    os.environ["PATH"] = local_ffmpeg_bin + os.pathsep + os.environ["PATH"]
else:
    print("System: No local FFmpeg found, relying on system PATH.")

def _bool_env(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _runtime_dir():
    configured = os.environ.get("OPENFREQUENCY_RUNTIME_DIR")
    if configured:
        os.makedirs(configured, exist_ok=True)
        return configured
    if getattr(__import__("sys"), "frozen", False):
        base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "OpenFrequency")
        os.makedirs(base, exist_ok=True)
        return base
    return os.path.dirname(os.path.abspath(__file__))


# Load config. Packaged builds keep config outside the exe so user secrets and
# local settings are never bundled into the executable.
CONFIG_PATH = os.environ.get("OPENFREQUENCY_CONFIG_PATH") or os.path.join(_runtime_dir(), 'config.json')
print(f"CONFIG_PATH resolved to: {CONFIG_PATH}")
config = {}
def load_config():
    global config
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            config = json.load(f)
    else:
        print("Warning: config.json not found, using defaults.")
    # Backfill ui.tutorial_completed for configs created before this field existed
    config.setdefault('ui', {}).setdefault('tutorial_completed', False)
    _sync_runtime_from_config()


def _first_non_empty(*values):
    """Return the first non-empty scalar value as a stripped string."""
    for value in values:
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            continue
        text = str(value).strip()
        if text and text.upper() not in {'N/A', 'NONE', 'NULL'}:
            return text
    return ""


def _extract_simbrief_callsign(data):
    """
    Extract a usable callsign from SimBrief JSON.

    Prefer the explicit ATC callsign if present. Fall back to ICAO airline +
    flight number, and finally registration/tail number.
    """
    general = data.get('general', {}) or {}
    params = data.get('params', {}) or {}
    api_params = data.get('api_params', {}) or {}
    aircraft = data.get('aircraft', {}) or {}

    explicit_callsign = _first_non_empty(
        general.get('atc_callsign'),
        general.get('callsign'),
        params.get('callsign'),
        api_params.get('callsign')
    )
    if explicit_callsign:
        return explicit_callsign.upper()

    airline = _first_non_empty(
        general.get('icao_airline'),
        params.get('airline'),
        api_params.get('airline')
    ).upper()
    flight_number = _first_non_empty(
        general.get('flight_number'),
        params.get('fltnum'),
        api_params.get('fltnum')
    ).replace(" ", "")
    if airline and flight_number:
        return f"{airline}{flight_number}".upper()

    registration = _first_non_empty(
        general.get('reg'),
        aircraft.get('reg'),
        params.get('reg'),
        api_params.get('reg')
    )
    if registration:
        return registration.upper()

    return ""


def _normalize_flight_plan(raw_flight_plan):
    """Normalize a flight plan payload/config block into the runtime shape."""
    raw_flight_plan = raw_flight_plan or {}
    return {
        "origin": _first_non_empty(raw_flight_plan.get('origin')).upper() or "N/A",
        "destination": _first_non_empty(raw_flight_plan.get('destination')).upper() or "N/A",
        "alternate": _first_non_empty(raw_flight_plan.get('alternate')).upper() or "N/A",
        "route": _first_non_empty(raw_flight_plan.get('route')) or "N/A",
        "cruise_alt": int(raw_flight_plan.get('cruise_alt', 0) or 0),
        "flight_number": _first_non_empty(raw_flight_plan.get('flight_number')).upper() or "N/A"
    }


def _active_career_job():
    # Free flight must never adopt the career callsign, even if a career job
    # is persisted in the profile. Gate strictly on session_mode.
    # Dict reads are atomic in CPython, so no lock is needed (and avoids
    # re-entering the non-reentrant context_lock when callers already hold it).
    if shared_context.get('session_mode') != 'career':
        return None
    profile_obj = globals().get('career_profile')
    if profile_obj:
        try:
            job = profile_obj.get_profile().get('active_job')
            if job and job.get('callsign'):
                return job
        except Exception:
            pass
    job = shared_context.get('active_job')
    if job and job.get('callsign'):
        return dict(job)
    return None


def _career_callsign_locked():
    return shared_context.get('session_mode') == 'career' and bool(_active_career_job())


def _current_runtime_callsign_from_config():
    career_job = _active_career_job()
    if career_job:
        return career_job.get('callsign')
    return config.get('user_profile', {}).get('callsign', 'N/A')


def _sync_runtime_from_config():
    """Sync selected config state into shared runtime context."""
    runtime_callsign = _current_runtime_callsign_from_config()
    with context_lock:
        shared_context['aircraft']['callsign'] = runtime_callsign
        shared_context['flight_plan'] = _normalize_flight_plan(config.get('flight_plan', {}))

    print(f"System: Callsign initialized to {shared_context['aircraft']['callsign']}")
    fp = shared_context['flight_plan']
    if fp.get('origin') != 'N/A' or fp.get('destination') != 'N/A':
        print(f"System: Flight plan initialized to {fp['origin']} -> {fp['destination']}")
        event_bus.emit('flight_plan_loaded', fp)

load_config()

# --- Career Mode Profile ---
career_profile = CareerProfile()
from core.career.evaluator import CareerEvaluator
career_evaluator = CareerEvaluator(config, career_profile, socketio)
career_evaluator.start()
airport_frequency_service = AirportFrequencyService(config)
airport_frequency_service.load()
ground_data_service = GroundDataService(config, airport_frequency_service=airport_frequency_service)
event_bus.on('config_updated', airport_frequency_service.update_config)
event_bus.on('config_updated', ground_data_service.update_config)

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
         print(f"Debug: Auth Check IP={client_ip} TokenPresent=True Status={status}", flush=True)
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
    
    # Check for mode parameter (from main menu)
    mode = request.args.get('mode')
    
    # 0. Main Menu (no mode selected)
    if not mode and not request.args.get('view'):
        return render_template('main_menu.html')
    
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

@app.route('/dashboard')
def dashboard():
    """Direct dashboard access (for Free Flight or Career mode)."""
    client_ip = request.remote_addr
    token = request.cookies.get('auth_token')
    perm = auth_manager.get_permission_level(client_ip, token)
    can_interact = perm in ['ADMIN', 'FULL']

    mode = request.args.get('mode', 'free')

    # Sync session_mode into shared_context so backend always agrees with URL param.
    # Career job acceptance already sets session_mode='career', but this also
    # handles the case where the server restarted and session_mode was lost.
    with context_lock:
        shared_context['session_mode'] = mode
        if mode == 'career':
            career_job = _active_career_job()
            if career_job and career_job.get('callsign'):
                shared_context['aircraft']['callsign'] = career_job['callsign']
                shared_context['callsign_override'] = career_job['callsign']
        else:
            # Free flight: remove any career callsign override, restore profile callsign
            shared_context.pop('callsign_override', None)
            free_cs = config.get('user_profile', {}).get('callsign', 'N/A')
            shared_context['aircraft']['callsign'] = free_cs

    career_evaluator.set_mode(mode == 'career')
    socketio.emit('flight_mode', {'mode': mode})

    return render_template('dashboard.html', can_interact=can_interact, permission=perm, flight_mode=mode)

@app.route('/get_my_permission')
def get_my_permission():
    """Returns current user's permission level."""
    client_ip = request.remote_addr
    token = request.cookies.get('auth_token')
    perm = auth_manager.get_permission_level(client_ip, token)
    can_interact = perm in ['ADMIN', 'FULL']
    return jsonify({"permission": perm, "can_interact": can_interact})

@app.route('/api/session_mode')
def get_session_mode():
    """Returns current flight session mode."""
    with context_lock:
        mode = shared_context.get('session_mode', None)
    return jsonify({"mode": mode})


@app.route('/api/nearby_frequencies')
def get_nearby_frequencies():
    with context_lock:
        nearby_airports = shared_context.get('environment', {}).get('nearby_airports', [])
        atc_state = dict(shared_context.get('atc_state', {}))
    return jsonify({
        "airports": nearby_airports,
        "active_frequency": atc_state.get('current_frequency', 0.0),
        "active_channel_key": atc_state.get('current_channel_key', ''),
        "current_controller": atc_state.get('current_controller', 'N/A')
    })


@app.route('/api/airport_data/refresh', methods=['POST'])
def refresh_airport_data():
    ok = airport_frequency_service.load_cached_or_download(force_update=True)
    if not ok:
        return jsonify({"status": "error", "message": "Failed to refresh airport data"}), 500

    if 'logic_manager' in globals():
        logic_manager._refresh_nearby_airports(force=True)

    return jsonify({"status": "success"})


@app.route('/api/ground_layout/<airport_ident>')
def get_ground_layout_api(airport_ident):
    airport_ident = (airport_ident or "").strip().upper()
    if not airport_ident:
        return jsonify({"status": "error", "message": "Missing airport ident"}), 400

    layout = ground_data_service.get_airport_layout(airport_ident)
    if not layout:
        return jsonify({"status": "error", "message": "Ground layout unavailable"}), 404

    nodes = {str(node.get("id")): node for node in layout.get("taxi_nodes", [])}
    edges = []
    for edge in layout.get("taxi_edges", []):
        start = nodes.get(str(edge.get("start")))
        end = nodes.get(str(edge.get("end")))
        if not start or not end:
            continue
        edges.append({
            "name": edge.get("name") or "",
            "kind": edge.get("kind") or "taxiway",
            "start": [start.get("lat"), start.get("lon")],
            "end": [end.get("lat"), end.get("lon")],
        })

    return jsonify({
        "status": "ok",
        "airport": airport_ident,
        "source": (layout.get("metadata") or {}).get("source") or layout.get("source_path") or "unknown",
        "edges": edges,
        "runways": layout.get("runways", []),
        "aprons": layout.get("aprons", []),
    })

@app.route('/api/flight_plan')
def get_flight_plan():
    """Return current flight plan with airport coordinates for map display."""
    with context_lock:
        fp = dict(shared_context.get('flight_plan', {}))
    if not fp or (fp.get('origin', 'N/A') == 'N/A' and fp.get('destination', 'N/A') == 'N/A'):
        return jsonify({"status": "empty"})
    for icao_key, coord_key in [('origin', 'origin_coords'), ('destination', 'dest_coords')]:
        if coord_key not in fp:
            icao = fp.get(icao_key, '')
            if icao and icao != 'N/A':
                pos = airport_frequency_service.get_airport_position(icao) if airport_frequency_service else None
                if pos:
                    fp[coord_key] = [pos['lat'], pos['lon']]
    return jsonify({"status": "ok", "flight_plan": fp})

@app.route('/api/resolve_route_waypoints', methods=['POST'])
def resolve_route_waypoints():
    """Resolve a route string to ordered [lat, lon] coordinates via nav SQLite."""
    data = request.get_json(force=True) or {}
    route_str = (data.get('route') or '').strip()
    origin = (data.get('origin') or '').strip().upper()
    destination = (data.get('destination') or '').strip().upper()
    if not route_str or route_str == 'N/A':
        return jsonify({'status': 'no_route', 'waypoints': []})

    sqlite_path = config.get('navdata', {}).get('sqlite_path', '')
    if not sqlite_path or 'path/to/db' in sqlite_path:
        return jsonify({'status': 'no_db', 'waypoints': []})

    try:
        import sqlite3
        conn = sqlite3.connect(sqlite_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        tokens = [t for t in route_str.split() if t and t != 'N/A']
        results = []
        for tok in tokens:
            ident = tok.upper()
            # Skip SID/STAR/airway fragments that are clearly not fix names (length > 7)
            if len(ident) > 7:
                continue
            # Try waypoint/fix table first, then VOR, then NDB, then airport
            row = None
            for table, id_col, lat_col, lon_col in [
                ('waypoint', 'ident', 'laty', 'lonx'),
                ('vor',      'ident', 'laty', 'lonx'),
                ('ndb',      'ident', 'laty', 'lonx'),
                ('airport',  'ident', 'laty', 'lonx'),
            ]:
                try:
                    cur.execute(f'SELECT laty, lonx FROM {table} WHERE ident=? LIMIT 1', (ident,))
                    row = cur.fetchone()
                    if row:
                        break
                except Exception:
                    continue
            if row:
                results.append({'ident': ident, 'lat': row[0], 'lon': row[1]})

        conn.close()
        return jsonify({'status': 'ok', 'waypoints': results})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e), 'waypoints': []})


@app.route('/api/taxi_route', methods=['POST'])
def request_taxi_route():
    """Manually trigger taxi route recalculation for current airport."""
    if not logic_manager:
        return jsonify({"status": "error", "message": "Logic manager not ready"}), 503
    logic_manager._refresh_ground_context()
    return jsonify({"status": "ok"})

@app.route('/api/xplane/traffic_targets')
def get_xplane_traffic_targets():
    global traffic_manager
    if traffic_manager is None:
        return jsonify({"targets": [], "count": 0, "source": "unavailable"})

    limit = request.args.get('limit', default=63, type=int)
    targets = traffic_manager.get_export_targets(limit=limit)
    return jsonify({
        "targets": targets,
        "count": len(targets),
        "source": "openfrequency_self_managed"
    })

@app.route('/api/locales/<locale>')
def get_locale(locale):
    """Serve locale files for frontend translation."""
    import os
    # Strip .json extension if present
    if locale.endswith('.json'):
        locale = locale[:-5]
    locale_path = os.path.join('data', 'locales', f'{locale}.json')
    if os.path.exists(locale_path):
        with open(locale_path, 'r', encoding='utf-8') as f:
            return f.read(), 200, {'Content-Type': 'application/json'}
    return jsonify({"error": "Locale not found", "path": locale_path}), 404

@app.route('/settings')
def settings_page():
    # Only ADMIN and FULL users can access settings
    client_ip = request.remote_addr
    token = request.cookies.get('auth_token')
    perm = auth_manager.get_permission_level(client_ip, token)
    
    if perm not in ['ADMIN', 'FULL']:
        return redirect('/')  # Redirect readonly users to dashboard
    
    return render_template('settings.html')

# --- Career Mode Routes ---
@app.route('/career')
def career_page():
    # Set career mode session
    mode = request.args.get('mode', 'career')
    return render_template('career_dashboard.html', flight_mode=mode)

@app.route('/career/profile')
def career_profile_api():
    profile = career_profile.get_profile()
    if profile.get('active_job'):
        profile['active_job'] = _career_job_for_client(profile.get('active_job'))
    return jsonify(profile)

def _career_current_airport():
    with context_lock:
        environment = shared_context.get('environment', {})
        flight_plan = shared_context.get('flight_plan', {})
        aircraft = shared_context.get('aircraft', {})
        candidates = [
            environment.get('current_airport'),
            environment.get('nearest_airport'),
            flight_plan.get('origin'),
            'ZBAA'
        ]
        lat = aircraft.get('latitude')
        lon = aircraft.get('longitude')
    ident = next((str(item).strip().upper() for item in candidates if item and str(item).strip().upper() != 'N/A'), 'ZBAA')
    if (not ident or ident == 'N/A') and lat and lon:
        ident = airport_frequency_service.get_nearest_airport_ident(lat, lon)
    return (ident or 'ZBAA').upper()

def _career_job_readiness(job):
    with context_lock:
        aircraft = dict(shared_context.get('aircraft', {}))
        nearest = (
            shared_context.get('environment', {}).get('current_airport')
            or shared_context.get('environment', {}).get('nearest_airport')
            or ''
        ).upper()

    origin = (job.get('origin') or '').upper()
    checks = []
    at_origin = nearest == origin if nearest and nearest != 'N/A' else False
    if not at_origin:
        lat = aircraft.get('latitude')
        lon = aircraft.get('longitude')
        airport = airport_frequency_service.get_airport_position(origin)
        if airport and lat and lon:
            distance_nm = airport_frequency_service._distance_nm(lat, lon, airport['lat'], airport['lon'])
            at_origin = distance_nm <= 5

    checks.append({
        'key': 'position',
        'ok': bool(at_origin),
        'message_key': 'check_position',
        'params': {'origin': origin, 'current': nearest or 'N/A'},
        'message': f"Move the aircraft to {origin} before starting this career flight. Current detected airport: {nearest or 'N/A'}."
    })
    checks.append({
        'key': 'on_ground',
        'ok': bool(aircraft.get('on_ground', False)),
        'message_key': 'check_on_ground',
        'message': "Aircraft should be on the ground."
    })
    checks.append({
        'key': 'stopped',
        'ok': float(aircraft.get('airspeed') or 0) < 5,
        'message_key': 'check_stopped',
        'message': "Aircraft should be stopped before dispatch."
    })

    assigned_aircraft = (job.get('aircraft') or '').upper()
    current_aircraft = (
        aircraft.get('aircraft_type')
        or aircraft.get('aircraft_icao')
        or aircraft.get('aircraft_title')
        or 'UNKNOWN'
    )
    current_aircraft_text = str(current_aircraft).upper()
    aircraft_matches = bool(assigned_aircraft and assigned_aircraft in current_aircraft_text)
    if not aircraft_matches:
        catalog = AircraftCatalog(config)
        detected = catalog.canonical_from_text(current_aircraft_text)
        aircraft_matches = detected == assigned_aircraft

    checks.append({
        'key': 'aircraft_type',
        'ok': aircraft_matches,
        'message_key': 'check_aircraft_type',
        'params': {'required': assigned_aircraft or 'N/A', 'current': current_aircraft_text or 'UNKNOWN'},
        'message': f"Use the assigned aircraft type {assigned_aircraft or 'N/A'}. Current detected aircraft: {current_aircraft_text or 'UNKNOWN'}."
    })

    combustion = bool(aircraft.get('combustion', False))
    n1 = float(aircraft.get('n1') or 0)
    cold_dark = (not combustion) and n1 < 5
    checks.append({
        'key': 'cold_dark',
        'ok': cold_dark,
        'message_key': 'check_cold_dark',
        'message': "Recommended start state: cold and dark at the gate."
    })
    return {
        'ready': all(item['ok'] for item in checks),
        'checks': checks,
        'aircraft': {
            'nearest_airport': nearest or 'N/A',
            'on_ground': bool(aircraft.get('on_ground', False)),
            'airspeed': round(float(aircraft.get('airspeed') or 0), 1),
            'n1': round(n1, 1),
            'combustion': combustion,
        }
    }

def _career_job_for_client(job):
    """Return an active career job copy with fields needed by current UI."""
    if not job:
        return None
    enriched = dict(job)
    if not enriched.get('route_source'):
        enriched['route_source'] = 'simbrief_recommended'
    if not enriched.get('airline_code'):
        callsign = (enriched.get('callsign') or '').upper()
        enriched['airline_code'] = callsign[:3] if len(callsign) >= 3 else ''
    if not enriched.get('simbrief_url'):
        from core.career.job_generator import JobGenerator
        job_gen = JobGenerator(career_profile, airport_service=airport_frequency_service, config=config)
        enriched['simbrief_url'] = job_gen.build_simbrief_url(enriched)
    return enriched

@app.route('/career/jobs')
def career_jobs_api():
    """Get available jobs from the job generator."""
    from core.career.job_generator import JobGenerator
    job_gen = JobGenerator(career_profile, airport_service=airport_frequency_service, config=config)
    current_airport = request.args.get('origin') or _career_current_airport()
    jobs = job_gen.generate_jobs(current_airport, count=8)
    # Cache jobs for later accept
    with context_lock:
        shared_context['cached_jobs'] = {j['id']: j for j in jobs}
    profile = career_profile.get_profile()
    return jsonify({
        'origin': current_airport,
        'jobs': jobs,
        'count': len(jobs),
        'current_airline': profile.get('current_airline'),
        'requires_contract': not bool(profile.get('current_airline')),
    })

@app.route('/career/readiness')
def career_readiness_api():
    """Return current active career job and preparation checklist."""
    job = _career_job_for_client(career_profile.get_profile().get('active_job'))
    if not job:
        return jsonify({'active_job': None, 'readiness': None})
    return jsonify({
        'active_job': job,
        'readiness': _career_job_readiness(job)
    })

@app.route('/career/airlines')
def career_airlines_api():
    from core.career.job_generator import JobGenerator
    job_gen = JobGenerator(career_profile, airport_service=airport_frequency_service, config=config)
    current_airport = request.args.get('origin') or _career_current_airport()
    return jsonify({
        'origin': current_airport,
        'airlines': job_gen.available_airlines(current_airport),
        'current_airline': career_profile.get_profile().get('current_airline'),
    })

@app.route('/career/sign_airline', methods=['POST'])
def career_sign_airline():
    from core.career.job_generator import JobGenerator
    data = request.get_json() or {}
    code = (data.get('code') or '').strip().upper()
    current_airport = data.get('origin') or _career_current_airport()
    job_gen = JobGenerator(career_profile, airport_service=airport_frequency_service, config=config)
    airlines = job_gen.available_airlines(current_airport)
    selected = next((airline for airline in airlines if airline['code'] == code), None)
    if not selected:
        return jsonify({'success': False, 'error': 'Airline is not available in this region'}), 400
    career_profile.set_airline(selected)
    return jsonify({'success': True, 'airline': selected})

@app.route('/career/accept_job', methods=['POST'])
def career_accept_job():
    """Accept a job and lock in the callsign."""
    from core.career.job_generator import JobGenerator
    data = request.get_json()
    job_id = data.get('job_id')
    
    if not job_id:
        return jsonify({'success': False, 'error': 'No job ID provided'}), 400
    
    # Get job from cache
    with context_lock:
        cached_jobs = shared_context.get('cached_jobs', {})
        job = cached_jobs.get(job_id)
    
    if job:
        job_gen = JobGenerator(career_profile, airport_service=airport_frequency_service, config=config)
        job_gen.accept_job(job)
        job = _career_job_for_client(job)
        readiness = _career_job_readiness(job)
        # Override callsign in shared context
        with context_lock:
            shared_context['callsign_override'] = job['callsign']
            shared_context['aircraft']['callsign'] = job['callsign']
            shared_context['active_job'] = job
            shared_context['flight_plan'] = _normalize_flight_plan({
                'origin': job.get('origin'),
                'destination': job.get('destination'),
                'cruise_alt': job.get('cruise_alt', 0),
                'flight_number': job.get('callsign'),
                'route': job.get('route') or 'DIRECT'
            })
            shared_context['session_mode'] = 'career'
        career_evaluator.set_mode(True)
        event_bus.emit('flight_plan_loaded', shared_context.get('flight_plan', {}))
        return jsonify({
            'success': True,
            'callsign': job['callsign'],
            'job': job,
            'readiness': readiness,
            'redirect_url': '/dashboard?mode=career'
        })
    else:
        return jsonify({'success': False, 'error': 'Job not found - please refresh job list'}), 404

@app.route('/career/licenses')
def career_licenses_api():
    """Get available licenses and requirements."""
    licenses = [
        {'id': 'PPL', 'name': 'Private Pilot License', 'price': 5000, 'required_xp': 500, 'required_hours': 10},
        {'id': 'CPL', 'name': 'Commercial Pilot License', 'price': 15000, 'required_xp': 2000, 'required_hours': 50},
        {'id': 'ATPL', 'name': 'Airline Transport Pilot License', 'price': 50000, 'required_xp': 10000, 'required_hours': 200},
    ]
    profile = career_profile.get_profile()
    owned = profile.get('licenses', ['P0'])
    for lic in licenses:
        lic['owned'] = lic['id'] in owned
        lic['can_buy'] = profile.get('money', 0) >= lic['price'] and profile.get('xp', 0) >= lic['required_xp']
    return jsonify(licenses)

@app.route('/career/buy_license', methods=['POST'])
def career_buy_license():
    """Purchase a license (deduct money, add to profile)."""
    data = request.get_json()
    license_id = data.get('license_id')
    
    licenses_data = {
        'PPL': {'price': 5000, 'required_xp': 500},
        'CPL': {'price': 15000, 'required_xp': 2000},
        'ATPL': {'price': 50000, 'required_xp': 10000},
    }
    
    if license_id not in licenses_data:
        return jsonify({'success': False, 'error': 'Invalid license'}), 400
    
    profile = career_profile.get_profile()
    lic = licenses_data[license_id]
    
    if profile.get('money', 0) < lic['price']:
        return jsonify({'success': False, 'error': 'Insufficient funds'}), 400
    if profile.get('xp', 0) < lic['required_xp']:
        return jsonify({'success': False, 'error': 'Insufficient experience'}), 400
    
    # Deduct money and add license
    career_profile.add_money(-lic['price'])
    if 'licenses' not in profile:
        profile['licenses'] = ['P0']
    profile['licenses'].append(license_id)
    career_profile.save_profile()
    
    return jsonify({'success': True, 'license': license_id, 'new_balance': career_profile.get_profile().get('money', 0)})

@app.route('/career/transactions')
def career_transactions_api():
    """Get bank transaction history."""
    profile = career_profile.get_profile()
    transactions = profile.get('transactions', [])
    return jsonify(transactions[-20:][::-1])  # Last 20 transactions, newest first

@app.route('/career/progress')
def career_progress_api():
    return jsonify(career_profile.get_next_rank_progress())

@app.route('/career/nickname', methods=['POST'])
def career_nickname():
    data = request.json or {}
    nickname = data.get('nickname', '').strip()
    if career_profile.update_nickname(nickname):
        return jsonify({"success": True, "nickname": nickname[:32]})
    return jsonify({"success": False, "error": "Invalid nickname"}), 400

@app.route('/career/callsign', methods=['POST'])
def career_callsign():
    if _career_callsign_locked():
        job = _active_career_job()
        return jsonify({
            "success": False,
            "error": "Career callsign is locked to the active job.",
            "callsign": job.get('callsign')
        }), 409
    data = request.json
    callsign = data.get('callsign', '').strip().upper()
    if callsign:
        career_profile.update_callsign(callsign)
        # Also update shared context
        with context_lock:
            shared_context['aircraft']['callsign'] = callsign
        return jsonify({"success": True, "callsign": callsign})
    return jsonify({"success": False, "error": "Invalid callsign"}), 400


# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# Telemetry, Feedback & Update routes
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/api/telemetry/recent_crashes')
def api_recent_crashes():
    """Return list of recent local crash logs (metadata only)."""
    mgr = _telemetry_mod.get_manager()
    crashes = mgr.get_recent_crashes(n=5)
    # Strip full traceback from list view to keep response small
    safe = [{k: v for k, v in c.items() if k != 'traceback'} for c in crashes]
    return jsonify(safe)


@app.route('/api/telemetry/upload_recent', methods=['POST'])
def api_upload_recent_crash():
    """Manually upload the most recent crash log to Cloudflare Workers."""
    mgr = _telemetry_mod.get_manager()
    crashes = mgr.get_recent_crashes(n=1)
    if not crashes:
        return jsonify({'ok': False, 'message': 'No crash logs found.'}), 404
    crash = crashes[0]
    crash_id = crash.get('crash_id', '')
    user_note = request.json.get('note') if request.is_json else None
    ok = mgr.upload_crash(crash_id, crash, user_note=user_note)
    if ok:
        return jsonify({'ok': True, 'message': f'Uploaded crash {crash_id[:8]}…'})
    return jsonify({'ok': False, 'message': 'Upload failed — check Workers URL in settings.'}), 502


@app.route('/api/models/download_stt', methods=['POST'])
def api_download_stt():
    import threading
    def _dl():
        import urllib.request
        url = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-whisper-tiny.en.tar.bz2'
        models_dir = os.path.join(os.path.dirname(__file__), 'models')
        os.makedirs(models_dir, exist_ok=True)
        try:
            dest = os.path.join(models_dir, 'sherpa-onnx-whisper-tiny.en.tar.bz2')
            urllib.request.urlretrieve(url, dest)
            import tarfile
            with tarfile.open(dest) as tf:
                tf.extractall(models_dir)
        except Exception as e:
            print(f'STT download error: {e}')
    threading.Thread(target=_dl, daemon=True).start()
    return jsonify({'status': 'ok', 'message': 'Downloading Whisper tiny.en model to ./models/ ...'})

@app.route('/api/models/download_tts', methods=['POST'])
def api_download_tts():
    import threading
    data = request.json or {}
    engine = data.get('engine', 'kokoro')
    def _dl():
        models_dir = os.path.join(os.path.dirname(__file__), 'models')
        os.makedirs(models_dir, exist_ok=True)
        try:
            import urllib.request
            if engine == 'kokoro':
                for name, url in [
                    ('kokoro-v0_19.onnx', 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/kokoro-v0_19.onnx'),
                    ('voices.bin', 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files/voices.bin'),
                ]:
                    urllib.request.urlretrieve(url, os.path.join(models_dir, name))
            elif engine == 'piper':
                for name, url in [
                    ('en_US-arctic-medium.onnx', 'https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/arctic/medium/en_US-arctic-medium.onnx'),
                    ('en_US-arctic-medium.onnx.json', 'https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/arctic/medium/en_US-arctic-medium.onnx.json'),
                ]:
                    urllib.request.urlretrieve(url, os.path.join(models_dir, name))
        except Exception as e:
            print(f'TTS download error: {e}')
    threading.Thread(target=_dl, daemon=True).start()
    engine_name = 'Kokoro-ONNX' if engine == 'kokoro' else 'Piper TTS'
    return jsonify({'status': 'ok', 'message': f'Downloading {engine_name} model to ./models/ ...'})

@app.route('/api/feedback', methods=['POST'])
def api_submit_feedback():
    """Forward user feedback to Cloudflare Workers (token stays server-side)."""
    data = request.get_json(force=True, silent=True) or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'ok': False, 'message': 'Title is required.'}), 400
    ok, feedback_id = _feedback_mod.submit_feedback(
        type_=data.get('type', 'other'),
        title=title,
        description=data.get('description', ''),
        crash_id=data.get('crash_id'),
        contact=data.get('contact'),
        include_log=bool(data.get('include_log', False)),
        include_config=bool(data.get('include_config', False)),
    )
    if ok:
        return jsonify({'ok': True, 'feedback_id': feedback_id}), 201
    return jsonify({'ok': False, 'message': feedback_id}), 502


@app.route('/api/update/check', methods=['POST'])
def api_update_check():
    """Synchronously check for updates and return result."""
    info = _updater_mod.check_update(socketio=socketio, silent=False)
    if info is None:
        current = _updater_mod._get_current_version()
        return jsonify({'update_available': False, 'current': current,
                        'message': 'Already up to date or Workers URL not configured.'})
    current = _updater_mod._get_current_version()
    return jsonify({
        'update_available': True,
        'current': current,
        'latest': info.get('latest'),
        'release_notes_en': info.get('release_notes_en', ''),
        'release_notes_zh': info.get('release_notes_zh', ''),
        'tag': info.get('tag', ''),
        'force_update': info.get('force_update', False),
    })


# Plugin Manager routes
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/plugins')
def plugins_page():
    return render_template('plugins.html')

def _pm():
    """Return the plugin manager, or raise a 503 if it isn't initialised yet."""
    if _plugin_manager is None:
        from flask import abort
        abort(503, description="Plugin manager not yet initialized")
    return _plugin_manager

@app.route('/api/plugins')
def api_list_plugins():
    return jsonify(_pm().list_plugins())

@app.route('/api/plugins/<plugin_id>/enable', methods=['POST'])
def api_enable_plugin(plugin_id):
    ok = _pm().enable(plugin_id)
    return jsonify({'ok': ok})

@app.route('/api/plugins/<plugin_id>/disable', methods=['POST'])
def api_disable_plugin(plugin_id):
    ok = _pm().disable(plugin_id)
    return jsonify({'ok': ok})

@app.route('/api/plugins/<plugin_id>', methods=['DELETE'])
def api_delete_plugin(plugin_id):
    ok = _pm().uninstall(plugin_id)
    return jsonify({'ok': ok})

@app.route('/api/plugins/install', methods=['POST'])
def api_install_plugin():
    """Install a plugin from an uploaded ZIP file."""
    f = request.files.get('file')
    if not f:
        return jsonify({'ok': False, 'message': 'No file uploaded'}), 400
    import tempfile
    # Close the temp file before saving on Windows — NamedTemporaryFile holds
    # an exclusive lock; f.save() would fail with PermissionError if still open.
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
            tmp_path = tmp.name
        f.save(tmp_path)
        pm = _pm()
        ok, msg = pm.install_from_zip(tmp_path)
        response = {'ok': ok, 'message': msg}
        if pm._last_install_warning:
            response['warning'] = pm._last_install_warning
            pm._last_install_warning = None
        return jsonify(response)
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e)}), 500
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

# ── DLC catalog & installer ───────────────────────────────────────────────────

@app.route('/api/dlc/catalog')
def api_dlc_catalog():
    """Return the bundled DLC catalog enriched with install status."""
    items = load_dlc_catalog()
    sim_cfg = config.get('simulator', {})
    for item in items:
        community = sim_cfg.get(item.get('config_key', ''), '')
        if community:
            import os as _os
            item['installed'] = _os.path.isdir(
                _os.path.join(community, item.get('install_subdir', item['id']))
            )
        else:
            item['installed'] = None   # unknown (community path not configured)
    return jsonify(items)

@app.route('/api/dlc/install/<dlc_id>', methods=['POST'])
def api_dlc_install(dlc_id):
    """Start async DLC install.  Poll /api/dlc/progress for status."""
    installer = get_installer(config)
    installer.install_async(dlc_id, socketio=socketio)
    return jsonify({'ok': True, 'message': f"Installing {dlc_id}…"})

@app.route('/api/dlc/progress')
def api_dlc_progress():
    return jsonify(current_progress())

# ── MSFS community folder setting ─────────────────────────────────────────────

@app.route('/api/browse_msfs_community', methods=['POST'])
def api_browse_msfs_community():
    """Return the best-guess MSFS Community folder if it exists."""
    import os
    candidates = [
        os.path.expandvars(r'%LOCALAPPDATA%\Packages\Microsoft.FlightSimulator_8wekyb3d8bbwe\LocalCache\Packages\Community'),
        os.path.expandvars(r'%APPDATA%\Microsoft Flight Simulator\Packages\Community'),
        r'C:\Users\Public\Documents\Microsoft Flight Simulator\Community',
    ]
    for c in candidates:
        if os.path.isdir(c):
            return jsonify({'found': True, 'path': c})
    return jsonify({'found': False, 'path': ''})

# ══════════════════════════════════════════════════════════════════════════════

@app.route('/quick_reply_templates')
def get_quick_reply_templates():
    """Return all quick-reply templates for the dashboard button panel."""
    from core.quick_reply import QuickReplyEngine
    return jsonify({
        'templates': QuickReplyEngine.all_templates(),
        'categories': QuickReplyEngine.categories(),
    })

@app.route('/get_config')
def get_config_route():
    load_config() # Reload from disk
    # Return safe copy with masked API key
    import copy
    config_safe = copy.deepcopy(config)
    if 'connection' in config_safe and 'api_key' in config_safe['connection']:
        if config_safe['connection']['api_key'] and len(config_safe['connection']['api_key']) > 5:
             config_safe['connection']['api_key'] = "******"
    # 告知前端呼号是否被职业模式锁定
    config_safe['_callsign_locked'] = _career_callsign_locked()
    return jsonify(config_safe)

@app.route('/get_cabin_media_packages')
def get_cabin_media_packages():
    """Return all available cabin media packages (including plugin-provided)."""
    from core.cabin_media_manager import cabin_media_manager

    # Load from cabin media manager (manifest.json)
    all_media = cabin_media_manager.all_media()
    packages = {}
    for entry in all_media:
        callsigns = entry.get('callsigns') or []
        if not callsigns:
            # Empty callsigns means generic/universal media
            callsigns = ['Generic']
        for callsign in callsigns:
            if callsign not in packages:
                packages[callsign] = {
                    'id': callsign,
                    'name': entry.get('name', callsign),
                    'name_zh': entry.get('name_zh', ''),
                    'voice': entry.get('voice', ''),
                    'source': entry.get('_source', 'builtin')
                }

    # Also load from scripts.json for cabin voice packages
    scripts_path = os.path.join('data', 'cabin', 'scripts.json')
    if os.path.exists(scripts_path):
        try:
            with open(scripts_path, 'r', encoding='utf-8') as f:
                scripts_data = json.load(f)
            for airline_code, config in scripts_data.items():
                if airline_code not in packages:
                    # Map airline codes to display names
                    airline_names = {
                        'Generic': {'name': 'Generic',            'name_zh': '通用'},
                        'CCA':     {'name': 'Air China',          'name_zh': '中国国际航空'},
                        'CES':     {'name': 'China Eastern',      'name_zh': '中国东方航空'},
                        'CES2':    {'name': 'China Eastern (EN)', 'name_zh': '中国东方航空（英语）'},
                        'CSN':     {'name': 'China Southern',     'name_zh': '中国南方航空'},
                        'CPA':     {'name': 'Cathay Pacific',     'name_zh': '国泰航空'},
                        'ANA':     {'name': 'All Nippon Airways', 'name_zh': '全日空'},
                        'JAL':     {'name': 'Japan Airlines',     'name_zh': '日本航空'},
                        'UAL':     {'name': 'United Airlines',    'name_zh': '美联航'},
                        'DAL':     {'name': 'Delta Air Lines',    'name_zh': '达美航空'},
                        'AAL':     {'name': 'American Airlines',  'name_zh': '美国航空'},
                    }
                    names = airline_names.get(airline_code, {'name': airline_code, 'name_zh': airline_code})
                    packages[airline_code] = {
                        'id': airline_code,
                        'name': names['name'],
                        'name_zh': names['name_zh'],
                        'voice': config.get('voice', ''),
                        'source': 'scripts'
                    }
        except Exception as e:
            print(f"Failed to load scripts.json: {e}")

    return jsonify({'packages': list(packages.values())})

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
    _sync_runtime_from_config()
            
    # Sync Security Mode
    if 'security' in config and 'mode' in config['security']:
         auth_manager.set_mode(config['security']['mode'])

    print("Settings saved.")
    event_bus.emit('config_updated', config)

    # 推送精简的配置摘要给所有前端，使其无需刷新即可同步状态
    socketio.emit('config_sync', {
        'ui':    config.get('ui', {}),
        'audio': {'radio_effect': bool(config.get('audio', {}).get('radio_effect', False))},
        'cabin': {'media_package': config.get('cabin', {}).get('media_package', '')},
    })

    # Apply Hoppie settings immediately if a logon code is configured
    hoppie_cfg = config.get('hoppie', {}) or {}
    hoppie_logon = (hoppie_cfg.get('logon_code') or '').strip()
    hoppie_interval = max(65, int(hoppie_cfg.get('poll_interval') or 65))
    if hoppie_logon:
        from core.hoppie_acars import hoppie_client
        hoppie_client.set_poll_interval(hoppie_interval)
        print(f"Hoppie: poll interval set to {hoppie_interval}s (logon code saved, connect via dashboard)")

    return jsonify({"status": "success"})

@app.route('/api/tutorial/status', methods=['GET'])
def tutorial_status():
    return jsonify({"completed": bool(config.get('ui', {}).get('tutorial_completed', False))})


@app.route('/api/tutorial/done', methods=['POST'])
def tutorial_done():
    """Mark the onboarding tutorial as completed and persist to config."""
    global config
    config.setdefault('ui', {})['tutorial_completed'] = True
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    socketio.emit('config_sync', {
        'ui':    config.get('ui', {}),
        'audio': {'radio_effect': bool(config.get('audio', {}).get('radio_effect', False))},
        'cabin': {'media_package': config.get('cabin', {}).get('media_package', '')},
    })
    return jsonify({"status": "ok"})


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
        callsign = _extract_simbrief_callsign(data)
        flight_plan = _normalize_flight_plan({
            "origin": origin,
            "destination": dest,
            "alternate": alt_icao,
            "route": route,
            "cruise_alt": cruise_alt,
            "flight_number": f"{airline}{flight_number}"
        })
        
        # Update Shared Context
        callsign_locked = _career_callsign_locked()
        with context_lock:
            shared_context['flight_plan'] = flight_plan
            if callsign and not callsign_locked:
                shared_context['aircraft']['callsign'] = callsign
            config['simbrief']['username'] = username
            config['simbrief']['last_fetched'] = time.time()
            config['flight_plan'] = flight_plan
            if callsign and not callsign_locked:
                config.setdefault('user_profile', {})['callsign'] = callsign
        
        # Save username to config implicitly
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        if callsign and not callsign_locked:
            print(f"SimBrief Callsign Imported: {callsign}")
        elif callsign_locked:
            print(f"SimBrief Callsign ignored in career mode; active job callsign remains {_active_career_job().get('callsign')}")
        print(f"Flight Plan Imported: {origin} -> {dest} via {route}")
        event_bus.emit('flight_plan_loaded', flight_plan)
        return jsonify({
            "status": "success", 
            "data": flight_plan,
            "callsign": callsign or None
        })

    except Exception as e:
        print(f"SimBrief Import Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# --- SocketIO Handlers ---
@socketio.on('disconnect')
def handle_disconnect():
    if 'logic_manager' in globals():
        event_bus.emit('atis_stop')


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

def _set_ptt_state(active):
    event = 'ptt_active' if active else 'ptt_released'
    event_bus.emit(event, {})
    socketio.emit('ptt_state', {'active': bool(active)})

def _dispatch_voice_data(audio_data, source='socket'):
    if not audio_data:
        print(f"PTT: Empty audio payload from {source}.")
        return False, "empty_audio"
    if 'stt_module' not in globals():
        print("PTT: STT module is not ready.")
        return False, "stt_unavailable"

    print(f"PTT: Received {len(audio_data)} bytes from {source}; dispatching STT.")
    socketio.emit('stt_status', {'status': 'processing', 'source': source})

    def _run():
        try:
            stt_module.transcribe(audio_data)
            socketio.emit('stt_status', {'status': 'done', 'source': source})
        except Exception as e:
            print(f"STT thread error: {e}")
            socketio.emit('stt_status', {'status': 'error', 'message': str(e), 'source': source})

    threading.Thread(target=_run, daemon=True).start()
    return True, "queued"

@socketio.on('voice_data')
def handle_voice_data(blob):
    """Receives voice data from the client and dispatches STT in a daemon thread.
    Running in a thread prevents a Sherpa-ONNX segfault from killing the Flask worker.
    """
    _dispatch_voice_data(blob, source='socket')

@socketio.on('ptt_active')
def handle_socket_ptt_active():
    _set_ptt_state(True)

@socketio.on('ptt_released')
def handle_socket_ptt_released():
    _set_ptt_state(False)

@app.route('/api/ptt_state', methods=['POST'])
def api_ptt_state():
    data = request.get_json(silent=True) or {}
    active = bool(data.get('active'))
    _set_ptt_state(active)
    return jsonify({'success': True, 'active': active})

@app.route('/api/voice_data', methods=['POST'])
def api_voice_data():
    audio_data = request.get_data(cache=False)
    ok, status = _dispatch_voice_data(audio_data, source='http')
    if not ok:
        return jsonify({'success': False, 'error': status}), 400 if status == 'empty_audio' else 503
    return jsonify({'success': True, 'status': status})

@socketio.on('text_input')
def handle_text_input(text):
    """
    Receives text input from the client and treats it as recognized speech.
    """
    print(f"Received text input: {text}")
    if 'logic_manager' in globals() and getattr(logic_manager, 'intercom_target', 'ATC') == 'CABIN':
        event_bus.emit('crew_message', {'text': text, 'target': 'all'})
        return
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
        print("Auth: Token created.", flush=True)
        
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


# --- Rescue Mode Routes ---
@app.route('/rescue')
def rescue_page():
    """Show rescue mode page with environment errors."""
    ok, errors = self_check()
    if ok:
        return redirect('/')
    return render_template('rescue_mode.html', errors=errors)

@app.route('/api/rescue/fix', methods=['POST'])
def rescue_fix():
    """Handle one-click fix requests (legacy, non-streaming)."""
    data = request.get_json()
    error_id = data.get('error_id', '')

    if error_id == 'ffmpeg':
        success, msg = download_ffmpeg()
        return jsonify({'success': success, 'message': msg})
    elif error_id in ('whisper', 'stt'):
        success, msg = download_stt_model()
        return jsonify({'success': success, 'message': msg})
    elif error_id == 'tts':
        success, msg = download_tts_model()
        return jsonify({'success': success, 'message': msg})

    return jsonify({'success': False, 'message': 'Unknown error type'})


@app.route('/api/model/download/<model_type>')
def model_download_sse(model_type):
    """
    Server-Sent Events endpoint for model downloads with real-time progress.
    model_type: 'stt' | 'tts'
    """
    import queue as _queue
    import json as _json

    q: _queue.Queue = _queue.Queue()

    def _progress(pct: int, msg: str):
        q.put({'progress': pct, 'message': msg})

    def _run():
        try:
            if model_type == 'stt':
                ok, final_msg = download_stt_model(_progress)
            elif model_type == 'tts':
                ok, final_msg = download_tts_model(_progress)
            else:
                ok, final_msg = False, '未知模型类型'
            q.put({'progress': 100 if ok else -1,
                   'message': final_msg,
                   'done': True,
                   'success': ok})
        except Exception as exc:
            q.put({'progress': -1,
                   'message': str(exc),
                   'done': True,
                   'success': False})

    threading.Thread(target=_run, daemon=True).start()

    def _generate():
        while True:
            item = q.get()
            yield f"data: {_json.dumps(item, ensure_ascii=False)}\n\n"
            if item.get('done'):
                break

    return Response(
        stream_with_context(_generate()),
        content_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# --- Flight Report Routes ---
@app.route('/report/latest')
def report_latest():
    """Serve the latest flight report."""
    import glob
    reports = glob.glob('data/reports/report_*.html')
    if reports:
        latest = max(reports, key=os.path.getctime)
        with open(latest, 'r', encoding='utf-8') as f:
            return f.read()
    return "No flight report available yet.", 404

@app.route('/report/img/<filename>')
@app.route('/reports/<path:filename>')
def serve_report(filename):
    """Serve generated flight reports."""
    from flask import send_from_directory
    # Ensure we look in the correct absolute path
    report_dir = os.path.join(os.getcwd(), 'data', 'reports')
    return send_from_directory(report_dir, filename)

def report_image(filename):
    """Serve report images."""
    from flask import send_from_directory
    return send_from_directory('data/reports/img', filename)


if __name__ == '__main__':
    packaged_mode = _bool_env("OPENFREQUENCY_PACKAGED", False)
    debug_mode = _bool_env("OPENFREQUENCY_DEBUG", not packaged_mode)
    host = os.environ.get("OPENFREQUENCY_HOST", "0.0.0.0")
    port = int(os.environ.get("OPENFREQUENCY_PORT", "5000"))

    # Read version from version.txt for unified version management
    version = "v3.9-beta"
    try:
        with open("version.txt", "r", encoding="utf-8") as f:
            version = f.read().strip()
    except Exception:
        pass

    print(f"--- Initializing OpenFrequency {version} ---")
    print(f"Debug: WERKZEUG_RUN_MAIN = {os.environ.get('WERKZEUG_RUN_MAIN')}")
    
    # Send daily usage heartbeat (fire-and-forget, privacy-preserving)
    _stats_mod.ping_async()

    # 0. Environment Self-Check (only in worker process)
    if packaged_mode or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        ok, errors = self_check()
        if not ok:
            print("⚠️ Environment check failed! Starting in rescue mode...")
            for e in errors:
                print(f"  - {e['title']}: {e['message']}")
            print("Open http://0.0.0.0:5000/rescue for repair options.")
    
    # 1. Initialize all core modules
    print("Initializing modules...")
    
    # Import new modules
    from core.black_box import BlackBox
    from core.flight_analyzer import FlightAnalyzer
    # from core.flight_report import FlightReport  # Deprecated in favor of BlackBox v2
    from core.head_tracker import HeadTracker
    from core.emergency_director import EmergencyDirector
    
    logic_manager = LogicManager(config, socketio, airport_frequency_service=airport_frequency_service, ground_service=ground_data_service)
    atc_monitor = ATCMonitor(config)
    sim_bridge = SimBridge(config, shared_context, context_lock, event_bus)

    # ── Cabin Media Manager ───────────────────────────────────────────────────
    from core.cabin_media_manager import cabin_media_manager as _cabin_media_mgr
    _cabin_media_mgr.attach_socketio(socketio)
    _cabin_media_mgr.load_builtin()
    _cabin_media_mgr.load_user()

    # Sync callsign changes to cabin media manager
    def _on_callsign_change(callsign):
        _cabin_media_mgr.set_callsign(callsign)
    event_bus.on('callsign_changed', _on_callsign_change)

    # ── Plugin Manager ────────────────────────────────────────────────────────
    global _plugin_manager
    _plugin_manager = PluginManager(config, socketio, event_bus, context_lock, shared_context)
    _plugin_manager.discover()
    logic_manager._plugin_manager = _plugin_manager
    logic_manager._sim_bridge     = sim_bridge

    # Forward telemetry to plugins (non-blocking; plugin hooks run synchronously
    # so keep them fast — heavy work should be threaded inside the plugin)
    def _fwd_telemetry_to_plugins(ctx):
        _plugin_manager.hook_telemetry(ctx)
    event_bus.on('telemetry_update', _fwd_telemetry_to_plugins)
    nav_manager = NavManager(config, shared_context, context_lock, event_bus, ground_service=ground_data_service, airport_frequency_service=airport_frequency_service)
    stt_module = STTLocal(config, event_bus)
    llm_client = LLMClient(config, shared_context, context_lock, event_bus, airport_frequency_service=airport_frequency_service)
    logic_manager._llm_client = llm_client  # Tier 2 fast-LLM access
    tts_engine = TTSEngine(config, socketio)
    atis_generator = ATISGenerator(config, socketio, airport_frequency_service=airport_frequency_service)
    traffic_manager = TrafficStateManager(config, sim_bridge, socketio)
    chatter_generator = ChatterGenerator(config, tts_engine)
    black_box = BlackBox(config)
    flight_analyzer = FlightAnalyzer(config, socketio)
    # flight_report = FlightReport(config, socketio, black_box) # Disabled
    head_tracker = HeadTracker(config, socketio)
    emergency_director = EmergencyDirector(config, socketio)
    
    # --- Crew Manager (Replaces CabinCrew and old Purser) ---
    
    # --- CabinCrew LLM Module ---
    crew_manager = CrewManager(config, llm_client, socketio)
    
    # --- ATC Handoff State Machine ---
    from core.atc_handoff import ATCHandoffManager
    atc_handoff = ATCHandoffManager(config, socketio)
    event_bus.emit('flight_plan_loaded', shared_context.get('flight_plan', {}))

    @socketio.on('set_flight_mode')
    def handle_flight_mode(data):
        """Toggle Career Mode/Free Flight."""
        # data = {'mode': 'career' | 'free'}
        mode = data.get('mode', 'free')
        enabled = (mode == 'career')
        career_evaluator.set_mode(enabled)
        # Update mode first, then resolve the right callsign for that mode and
        # write it back atomically. Without this, switching from career to
        # free flight would leave the career callsign stuck in shared_context.
        with context_lock:
            shared_context['session_mode'] = mode
        if enabled:
            job = _active_career_job()
            with context_lock:
                if job and job.get('callsign'):
                    shared_context['callsign_override'] = job['callsign']
                    shared_context['aircraft']['callsign'] = job['callsign']
        else:
            free_cs = config.get('user_profile', {}).get('callsign', 'N/A')
            with context_lock:
                shared_context.pop('callsign_override', None)
                shared_context.pop('active_job', None)
                shared_context['aircraft']['callsign'] = free_cs
        # Notify all clients
        socketio.emit('game_mode_changed', {'mode': mode})

    @socketio.on('toggle_intercom')
    def handle_toggle_intercom(data):
        """Toggle PTT target between ATC and Cabin crew."""
        # data = {'target': 'ATC' | 'CABIN'}
        target = data.get('target', 'ATC')
        logic_manager.intercom_target = target
        print(f"LogicManager: Intercom target set to {target}")
        # Notify clients to update UI (red border for cabin mode)
        socketio.emit('intercom_mode_changed', {'target': target})

    @socketio.on('set_flight_rules')
    def handle_flight_rules(data):
        """Set IFR/VFR flight rules for ATC guidance."""
        rules = data.get('rules', 'IFR').upper()
        if rules not in ('IFR', 'VFR'):
            rules = 'IFR'
        with context_lock:
            shared_context['flight_rules'] = rules
        print(f"LogicManager: Flight rules set to {rules}")

    # ── CPDLC socket handlers ─────────────────────────────────────────────────
    from core.cpdlc_manager import cpdlc_manager

    # Forward CPDLC events to all connected clients
    def _cpdlc_downlink_fwd(msg_dict):
        socketio.emit('cpdlc_downlink', msg_dict)

    def _cpdlc_uplink_fwd(msg_dict):
        socketio.emit('cpdlc_uplink', msg_dict)

    def _cpdlc_session_fwd(info):
        socketio.emit('cpdlc_session', info)

    event_bus.on('cpdlc_downlink',      _cpdlc_downlink_fwd)
    event_bus.on('cpdlc_uplink',        _cpdlc_uplink_fwd)
    event_bus.on('cpdlc_session_change', _cpdlc_session_fwd)

    @socketio.on('cpdlc_send')
    def handle_cpdlc_send(data):
        """
        Pilot sends a CPDLC downlink message.
        data: { "msg_type": "REQUEST_CLIMB", "level": "FL350" }
              or { "msg_type": "FREE_TEXT", "text": "..." }
        """
        msg_type = data.pop('msg_type', 'FREE_TEXT')
        if msg_type == 'FREE_TEXT':
            mrn = cpdlc_manager.send_free_text(data.get('text', ''))
        else:
            mrn = cpdlc_manager.send_downlink(msg_type, **data)
        emit('cpdlc_sent', {'mrn': mrn})

    @socketio.on('cpdlc_respond')
    def handle_cpdlc_respond(data):
        """
        Pilot acknowledges an uplink: WILCO / UNABLE / ROGER / STANDBY.
        data: { "uplink_mrn": 3, "response": "WILCO" }
        """
        new_mrn = cpdlc_manager.pilot_respond(
            data.get('uplink_mrn'), data.get('response', 'WILCO')
        )
        emit('cpdlc_sent', {'mrn': new_mrn})

    @socketio.on('cpdlc_logon')
    def handle_cpdlc_logon(data):
        """Initiate CPDLC logon. data: { "facility": "ZBPE" }"""
        mrn = cpdlc_manager.logon(data.get('facility', 'ATC'))
        emit('cpdlc_sent', {'mrn': mrn})

    @socketio.on('cpdlc_logoff')
    def handle_cpdlc_logoff(_data):
        mrn = cpdlc_manager.logoff()
        emit('cpdlc_sent', {'mrn': mrn})

    @socketio.on('cpdlc_history')
    def handle_cpdlc_history(_data):
        emit('cpdlc_history', {'messages': cpdlc_manager.get_history()})

    # ── Radar vector mode toggle ──────────────────────────────────────────────
    @socketio.on('set_radar_vector_mode')
    def handle_radar_vector_mode(data):
        """
        Enable / disable radar vector mode.
        data: { "enabled": true|false }
        When enabled, ATC heading/altitude/speed cards are automatically
        pushed to the simulator autopilot.
        """
        enabled = bool(data.get('enabled', False))
        logic_manager.radar_vector_mode = enabled
        print(f"LogicManager: Radar vector mode {'ON' if enabled else 'OFF'}")
        emit('radar_vector_mode', {'enabled': enabled})

    @socketio.on('manual_radar_vector')
    def handle_manual_radar_vector(data):
        """
        Issue an immediate autopilot command from the dashboard without
        going through the LLM.
        data: { "type": "HDG"|"ALT"|"SPD", "value": <number> }
        """
        vtype = data.get('type', '').upper()
        value = data.get('value')
        if value is None:
            return
        try:
            value = float(value)
        except (TypeError, ValueError):
            return
        if vtype == 'HDG':
            sim_bridge.set_autopilot_heading(value)
        elif vtype == 'ALT':
            sim_bridge.set_autopilot_altitude(value)
        elif vtype == 'SPD':
            sim_bridge.set_autopilot_speed(value)

    # ── Quick Reply socket handler ────────────────────────────────────────────
    @socketio.on('quick_reply')
    def handle_quick_reply(data):
        """
        UI sends { template_id: "readback_correct", vars: { alt: "FL350" } }
        Logic manager renders the template and broadcasts as an ATC response.
        """
        template_id = data.get('template_id', '')
        extra_vars  = data.get('vars', {})
        if template_id:
            logic_manager.handle_quick_reply(template_id, extra_vars or None)

    # ── Hoppie ACARS socket handlers ──────────────────────────────────────────
    @socketio.on('hoppie_logon')
    def handle_hoppie_logon(data):
        from core.hoppie_acars import hoppie_client
        # Allow dashboard to omit logon code — fall back to saved config
        logon = (data.get('logon') or '').strip() or (config.get('hoppie', {}) or {}).get('logon_code', '')
        callsign = data.get('callsign', 'OFTEST')
        # Apply saved poll interval before starting
        saved_interval = max(65, int((config.get('hoppie', {}) or {}).get('poll_interval') or 65))
        hoppie_client.set_poll_interval(saved_interval)
        result = hoppie_client.logon(logon, callsign)
        emit('hoppie_status', {'connected': result, 'message': 'Connected to Hoppie ACARS' if result else 'Logon failed — check your code at hoppie.nl'})
        if result:
            hoppie_client.start_polling(socketio)

    @socketio.on('hoppie_logoff')
    def handle_hoppie_logoff(data):
        from core.hoppie_acars import hoppie_client
        hoppie_client.logoff()
        emit('hoppie_status', {'connected': False, 'message': 'Disconnected'})

    @socketio.on('hoppie_send')
    def handle_hoppie_send(data):
        from core.hoppie_acars import hoppie_client
        to = data.get('to', '')
        text = data.get('text', '')
        ok = hoppie_client.send_telex(to, text)
        emit('hoppie_message', {'dir': 'out', 'from': hoppie_client.callsign, 'to': to, 'packet': text, 'ts': None})

    @socketio.on('tune_frequency')
    def handle_tune_frequency(data):
        frequency = data.get('frequency')
        if frequency is None:
            return
        try:
            frequency = round(float(frequency), 3)
        except Exception:
            return

        tuned = sim_bridge.set_com1_frequency(frequency)
        logic_manager.switch_frequency_context(frequency, source='ui')
        with context_lock:
            shared_context['aircraft']['com1_freq'] = frequency
        socketio.emit('frequency_tuned', {'frequency': frequency, 'tuned': tuned})

    @socketio.on('cabin_media_play')
    def handle_cabin_media_play(data):
        """Frontend requests playback of a cabin media entry by id."""
        media_id = data.get('id', '')
        if media_id:
            _cabin_media_mgr.play(media_id)
            _plugin_manager.hook_cabin_media_play(media_id)

    @socketio.on('cabin_media_list')
    def handle_cabin_media_list(data):
        """Frontend requests the current cabin media list for the active callsign."""
        callsign = data.get('callsign', '')
        items = _cabin_media_mgr.media_for_callsign(callsign)
        safe = [e for e in items if e.get('file')]  # only entries with a file
        emit('cabin_media_updated', {'media': safe})

    @socketio.on('cabin_intercom')
    def handle_cabin_intercom(data):
        """Handle intercom requests from UI."""
        # data = {'action': 'call_purser' | 'prepare_cabin' | 'emergency' | 'status' | 'chat'}
        action = data.get('action')
        if action:
            event_bus.emit('cabin_intercom', action)
            # Also trigger CrewManager module
            if action in ['boarding', 'deboarding', 'stop_ambience', 'welcome', 'safety_demo', 'takeoff_prep', 'climb_service', 'descent', 'arrival_prep', 'turbulence']:
                event_bus.emit('cabin_crew_request', action)
    
    @socketio.on('cabin_chat')
    def handle_cabin_chat(data):
        """Handle cabin crew chat messages."""
        message = data.get('message', '')
        if message:
            event_bus.emit('user_cabin_message', message)
    
    @socketio.on('crew_message')
    def handle_crew_message(data):
        """Handle pilot to crew messages (from channel selector)."""
        # data = {'text': str, 'target': 'fo' | 'purser' | 'all'}
        if 'logic_manager' in globals():
            logic_manager.intercom_target = 'CABIN'
        event_bus.emit('crew_message', data)

    def handle_simulator_failure_event(data):
        event_name = data.get('event')
        result = data.get('result', {})
        ok = False
        if event_name:
            ok = sim_bridge.trigger_failure_event(event_name)
        if isinstance(result, dict):
            result['ok'] = ok

    event_bus.on('simulator_failure_event', handle_simulator_failure_event)

    # Feature 2.4: Debug Kit Runtime Updates
    @socketio.on('update_debug_config')
    def handle_debug_config(data):
        """Handle runtime debug changes."""
        # data = {'infinite_pattern': bool, 'voice_override': str}
        print(f"Debug: Runtime config update -> {data}")
        
        if 'infinite_pattern' in data:
            logic_manager.infinite_pattern = data['infinite_pattern']
            # Restart/Stop scheduler job if needed? 
            # LogicManager checks the flag in the loop, so just updating flag is enough.
            
        if 'accent_override' in data:
            voice = data['accent_override']
            tts_engine.set_voice_override(voice)

    # ── Updater socket handlers ──────────────────────────────────────────────

    @socketio.on('start_update_download')
    def handle_start_download(data):
        """Begin downloading the latest update in a background thread."""
        _updater_mod.set_socketio(socketio)
        asset_key = (data or {}).get('asset_key', 'win_x64')
        threading.Thread(
            target=_updater_mod.download_update,
            args=(asset_key, socketio),
            daemon=True,
            name='OF-Updater-Download'
        ).start()

    @socketio.on('install_update')
    def handle_install_update(data):
        """Launch the downloaded installer and exit."""
        ok = _updater_mod.launch_installer()
        if not ok:
            socketio.emit('update_download_failed', {'reason': 'Installer not found. Download first.'})

    # 2. Start all background threads

    # 2. Start all background threads
    # CRITICAL: Only start services in the worker process (reloader child), not the parent.
    if packaged_mode or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        print("Starting background services (Worker Process)...")

        # Telemetry: pass socketio so updater can emit events; check update after 8s
        _updater_mod.set_socketio(socketio)
        def _delayed_update_check():
            time.sleep(8)
            try:
                _updater_mod.check_update(socketio=socketio, silent=True)
            except Exception as e:
                print(f"Updater: startup check failed — {e}")
        threading.Thread(target=_delayed_update_check, daemon=True, name='OF-UpdateCheck').start()

        logic_manager.start()
        atc_monitor.start()
        sim_bridge.start()
        nav_manager.start()
        traffic_manager.start()
        head_tracker.start()
        emergency_director.start()

        # Initialize and start the scheduler
        scheduler = BackgroundScheduler()
        scheduler.start()
        
        # Pass scheduler to LogicManager
        logic_manager.set_scheduler(scheduler)
    else:
        print("System: Parent process started. Waiting for reloader to spawn worker...")

    # 3. Start the Web Server
    print(f"Starting Web Server on http://localhost:{port}")
    if host == "0.0.0.0":
        import socket as _sock
        try:
            lan_ip = _sock.gethostbyname(_sock.gethostname())
        except Exception:
            lan_ip = "<your-lan-ip>"
        print(f"LAN access: http://{lan_ip}:{port}")
    socketio.run(app, host=host, port=port, debug=debug_mode, allow_unsafe_werkzeug=True, use_reloader=debug_mode)
