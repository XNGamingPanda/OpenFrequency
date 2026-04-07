"""
Crew Communication Module - 机组通信模块
副驾驶 (First Officer) + 乘务长 (Purser) 双角色系统
"""
import threading
import random
import os
import csv
import json
from pathlib import Path
from datetime import datetime
from .context import shared_context, context_lock, event_bus


class CrewMember:
    """Base class for crew member."""
    
    def __init__(self, role, names, llm_client, socketio, config):
        self.role = role  # 'first_officer' or 'purser'
        self.name = random.choice(names)
        self.llm_client = llm_client
        self.socketio = socketio
        self.config = config
        self.voice = None
    
    def send_message(self, message, urgent=False, log_to_file=True):
        """Send message to cockpit."""
        sender = f"{self.role.replace('_', ' ').title()} ({self.name})"
        
        self.socketio.emit('chat_log', {
            'sender': sender,
            'text': message,
            'urgent': urgent,
            'role': self.role
        })
        
        if log_to_file:
            _log_to_csv(sender, message)
        
        return sender, message


class FirstOfficer(CrewMember):
    """副驾驶 - 在驾驶舱，可听见ATC和机组通讯。"""
    
    FO_NAMES = [
        "David", "Michael", "John", "Chris", "James",
        "小李", "小王", "小张", "小刘", "小陈"
    ]
    
    FO_RESPONSES = [
        "Roger that, Captain.",
        "Copy, I'll handle it.",
        "明白，机长。",
        "收到，我来处理。",
        "Understood. Adjusting now.",
        "好的，正在调整。"
    ]
    
    def __init__(self, llm_client, socketio, config):
        super().__init__('first_officer', self.FO_NAMES, llm_client, socketio, config)
        self.voice = config.get('crew', {}).get('first_officer_voice', 'en-US-GuyNeural')
        print(f"FirstOfficer: Initialized - {self.name}")
    
    def respond_to_atc(self, atc_message):
        """副驾驶监听ATC但不一定回复（仅内部处理）。"""
        # FO hears ATC but doesn't always respond visually
        # This is internal processing only
        pass
    
    def assist_pilot(self, request):
        """协助机长请求。"""
        response = random.choice(self.FO_RESPONSES)
        return self.send_message(response)


class Purser(CrewMember):
    """乘务长 - 在客舱，只能听见机组通讯，听不见ATC。"""
    
    PURSER_NAMES = [
        "Emily", "Sarah", "Lisa", "Jennifer", "Anna",
        "小雪", "小雨", "小美", "小玲", "小婷"
    ]
    
    IDLE_MESSAGES = [
        "机长，后舱一切正常。",
        "Captain, cabin is secure. Passengers are settled.",
        "机长，乘客们都很安静，没有特殊情况。",
        "Sir, we're about to begin service. Anything you need?",
        "机长，我们准备开始送餐了。",
        "Captain, we have a nervous first-time flyer. I'll keep an eye on them."
    ]
    
    EMERGENCY_ALERTS = [
        "机长！后舱有乘客晕倒了！需要紧急降落！",
        "CAPTAIN! Medical emergency in the cabin! Passenger unconscious!",
        "机长，后舱有人抽搐！需要医疗支援！",
        "Captain! We have a fire in the galley! Smoke detected!",
        "机长！有乘客突发心脏病！请求优先降落！",
        "MAYDAY! Captain, we've got smoke in the cabin!"
    ]
    
    def __init__(self, llm_client, socketio, config):
        super().__init__('purser', self.PURSER_NAMES, llm_client, socketio, config)
        self.voice = config.get('crew', {}).get('purser_voice', 'en-US-JennyNeural')
        print(f"Purser: Initialized - {self.name}")
    
    def report_status(self):
        """状态报告。"""
        msg = random.choice(self.IDLE_MESSAGES)
        return self.send_message(msg)
    
    def emergency_alert(self, emergency_type='unknown'):
        """紧急情况报告。"""
        if emergency_type == 'medical':
            alerts = [m for m in self.EMERGENCY_ALERTS if 'medical' in m.lower() or '心脏' in m or '晕倒' in m]
        elif emergency_type == 'fire':
            alerts = [m for m in self.EMERGENCY_ALERTS if 'fire' in m.lower() or '火' in m or 'smoke' in m.lower()]
        else:
            alerts = self.EMERGENCY_ALERTS
        
        msg = random.choice(alerts) if alerts else random.choice(self.EMERGENCY_ALERTS)
        return self.send_message(msg, urgent=True)


class CrewManager:
    """
    机组管理器 - 管理副驾驶和乘务长的统一接口。
    """
    
    def __init__(self, config, llm_client, socketio):
        self.config = config
        self.llm_client = llm_client
        self.socketio = socketio
        self.enabled = config.get('cabin_crew', {}).get('enabled', True)
        self.cabin_scripts = self._load_cabin_scripts()
        self.airline_code = config.get('cabin', {}).get('airline') or config.get('user_profile', {}).get('airline_icao', 'Generic')
        self.last_proactive_at = 0.0
        self.proactive_min_interval = float(config.get('crew', {}).get('proactive_min_interval_sec', 90))
        self.proactive_flags = {
            'boarding_ready': False,
            'takeoff_ready': False,
            'service_ready': False,
            'descent_ready': False,
            'arrival_ready': False,
        }
        
        # 初始化两个角色
        self.first_officer = FirstOfficer(llm_client, socketio, config)
        self.purser = Purser(llm_client, socketio, config)
        
        # 订阅事件
        event_bus.on('crew_message', self.on_crew_message)
        event_bus.on('cabin_crew_request', self.on_crew_request)
        event_bus.on('emergency_alert', self.on_emergency)
        event_bus.on('telemetry_update', self.on_telemetry_update)
        
        print(f"CrewManager: Initialized with FO={self.first_officer.name}, Purser={self.purser.name}")

    def _load_cabin_scripts(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'cabin', 'scripts.json')
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"CrewManager: Failed to load cabin scripts - {e}")
        return {}

    def _get_cabin_script(self, script_key, fallback_text):
        airline_data = self.cabin_scripts.get(self.airline_code, self.cabin_scripts.get('Generic', {}))
        script_entry = airline_data.get(script_key)
        voice = airline_data.get('voice')

        if isinstance(script_entry, dict):
            return {
                'text': script_entry.get('text') or fallback_text,
                'voice': script_entry.get('voice') or voice,
                'audio': self._resolve_media_path(script_entry.get('audio')),
                'video': self._resolve_media_path(script_entry.get('video'))
            }

        return {
            'text': script_entry or fallback_text,
            'voice': voice,
            'audio': None,
            'video': None
        }

    def _resolve_media_path(self, media_path):
        if not media_path:
            return None

        media_path = str(media_path).strip().replace('\\', '/')
        if not media_path:
            return None

        if media_path.startswith('/static/'):
            return media_path

        if media_path.startswith('static/'):
            return '/' + media_path

        if media_path.startswith('cabin_media/'):
            return '/static/' + media_path

        candidate = Path(os.path.dirname(os.path.dirname(__file__))) / 'static' / media_path
        if candidate.exists():
            return '/static/' + media_path.replace('\\', '/').lstrip('/')

        return None

    def _broadcast_cabin_announcement(self, script_key, fallback_text):
        script = self._get_cabin_script(script_key, fallback_text)
        text = script.get('text') or fallback_text
        voice = script.get('voice')
        audio_url = script.get('audio')
        video_url = script.get('video')
        self.socketio.emit('chat_log', {
            'sender': f"Cabin PA ({self.purser.name})",
            'text': text,
            'role': 'cabin_pa'
        })
        if audio_url or video_url:
            self.socketio.emit('cabin_media_playback', {
                'text': text,
                'audio_url': audio_url,
                'video_url': video_url
            })
        else:
            event_bus.emit('chatter_tts_request', {
                'text': text,
                'voice': voice,
                'is_atc': False,
                'priority': 2
            })
    
    def on_crew_message(self, data):
        """
        处理机长发给机组的消息。
        target: 'fo' / 'purser' / 'all'
        """
        if not self.enabled:
            return
        
        text = data.get('text', '')
        target = data.get('target', 'all')
        if text:
            self.socketio.emit('chat_log', {
                'sender': 'Pilot',
                'text': text,
                'role': 'pilot'
            })
        
        # 使用 LLM 生成回复
        if target in ['fo', 'all']:
            self._llm_respond(self.first_officer, text)
        if target in ['purser', 'all']:
            self._llm_respond(self.purser, text)
    
    def on_crew_request(self, request_type):
        """处理按钮触发的请求。"""
        if not self.enabled:
            return
        
        if request_type == 'welcome':
            self._broadcast_cabin_announcement('welcome', "Ladies and gentlemen, welcome aboard. Please fasten your seatbelts and ensure all carry-on items are stowed.")
        elif request_type == 'boarding':
            self._broadcast_cabin_announcement('welcome', "Boarding is now in progress. Cabin crew, please prepare the cabin for passenger boarding.")
            self.purser.send_message("Boarding announcement sent, Captain.")
        elif request_type == 'safety_demo':
            self._broadcast_cabin_announcement('safety_demo', "Please pay attention to the safety demonstration.")
        elif request_type == 'takeoff_prep':
            self._broadcast_cabin_announcement('takeoff_prep', "Cabin crew, seats for takeoff.")
        elif request_type == 'climb_service':
            self._broadcast_cabin_announcement('climb_service', "We will now begin our inflight service.")
        elif request_type == 'turbulence':
            self._broadcast_cabin_announcement('turbulence', "Ladies and gentlemen, due to expected turbulence, please return to your seats and fasten your seatbelts immediately.")
        elif request_type == 'descent':
            self._broadcast_cabin_announcement('descent', "We are beginning our descent. Please return to your seats.")
        elif request_type == 'arrival_prep':
            self._broadcast_cabin_announcement('arrival_prep', "Cabin crew, prepare the cabin for arrival.")
        elif request_type == 'deboarding':
            self._broadcast_cabin_announcement('deboarding', "Deboarding is now in progress. Thank you for flying with us today.")
            self.purser.send_message("Deboarding announcement sent, Captain.")
        elif request_type == 'stop_ambience':
            self.socketio.emit('stop_ambience')
            self.purser.send_message("Cabin audio stopped.")
    
    def on_emergency(self, data):
        """紧急情况通知机组。"""
        if not self.enabled:
            return
        
        emergency_type = data.get('type', 'unknown')
        self.purser.emergency_alert(emergency_type)

    def _speak_as_crew(self, crew_member, message):
        event_bus.emit('chatter_tts_request', {
            'text': message,
            'voice': getattr(crew_member, 'voice', None),
            'is_atc': False,
            'priority': 2
        })

    def _maybe_proactive_message(self, key, crew_member, message):
        now = datetime.now().timestamp()
        if self.proactive_flags.get(key):
            return
        if now - self.last_proactive_at < self.proactive_min_interval:
            return
        self.proactive_flags[key] = True
        self.last_proactive_at = now
        crew_member.send_message(message)
        self._speak_as_crew(crew_member, message)

    def on_telemetry_update(self, data):
        if not self.enabled:
            return

        aircraft = data.get('aircraft', data) or {}
        altitude = float(aircraft.get('altitude', 0) or 0)
        airspeed = float(aircraft.get('airspeed', 0) or 0)
        on_ground = bool(aircraft.get('on_ground', True))
        vertical_speed = float(aircraft.get('vs', aircraft.get('vertical_speed', 0)) or 0)

        if on_ground and airspeed < 3:
            self._maybe_proactive_message(
                'boarding_ready',
                self.purser,
                "Captain, cabin is ready. Boarding is complete and all passengers are seated."
            )

        if not on_ground and altitude > 300 and vertical_speed > 300:
            self._maybe_proactive_message(
                'takeoff_ready',
                self.first_officer,
                "Captain, positive climb established. After takeoff checks are complete."
            )

        if not on_ground and altitude > 10000:
            self._maybe_proactive_message(
                'service_ready',
                self.purser,
                "Captain, cabin is released. We are ready to begin inflight service."
            )

        if not on_ground and vertical_speed < -300 and altitude < 12000:
            self._maybe_proactive_message(
                'descent_ready',
                self.purser,
                "Captain, cabin crew has been advised. We are preparing the cabin for descent."
            )

        if on_ground and airspeed < 25 and self.proactive_flags.get('descent_ready'):
            self._maybe_proactive_message(
                'arrival_ready',
                self.purser,
                "Captain, cabin secure. Passengers will remain seated until the seatbelt sign is off."
            )
    
    def _llm_respond(self, crew_member, user_message):
        """使用 LLM 生成机组回复。"""
        
        def _generate():
            try:
                with context_lock:
                    altitude = shared_context['aircraft'].get('altitude', 0)
                    phase = "taxiing" if altitude < 100 else "cruising" if altitude > 10000 else "climbing/descending"
                
                role_desc = "First Officer in the cockpit" if crew_member.role == 'first_officer' else "Purser in the cabin"
                
                system_prompt = f"""
                You are {crew_member.name}, a professional {role_desc} on this flight.
                Current flight phase: {phase}
                Your role:
                - Be helpful, professional, and brief
                - Keep responses under 30 words
                - Reply in the SAME LANGUAGE as the pilot's message
                """
                
                response = self.llm_client._call_llm_sync(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    max_tokens=80
                )
                
                if response:
                    crew_member.send_message(response.strip())
                    self._speak_as_crew(crew_member, response.strip())
                    
            except Exception as e:
                print(f"CrewManager: LLM error: {e}")
                if crew_member.role == 'purser':
                    sender, fallback = crew_member.report_status()
                    self._speak_as_crew(crew_member, fallback)
                else:
                    sender, fallback = crew_member.assist_pilot(user_message)
                    self._speak_as_crew(crew_member, fallback)
        
        threading.Thread(target=_generate, daemon=True).start()


def _log_to_csv(sender, message):
    """保存机组对话到 CSV 文件。"""
    try:
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        
        date_str = datetime.now().strftime("%Y%m%d")
        filename = os.path.join(log_dir, f"cabin_{date_str}.csv")
        
        file_exists = os.path.exists(filename)
        
        with open(filename, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(['timestamp', 'sender', 'message'])
            writer.writerow([datetime.now().isoformat(), sender, message])
    except Exception as e:
        print(f"CrewManager: Log error: {e}")
