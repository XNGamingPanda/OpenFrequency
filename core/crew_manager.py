"""
Crew Communication Module - 机组通信模块
副驾驶 (First Officer) + 乘务长 (Purser) 双角色系统
"""
import threading
import random
import os
import csv
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
        
        # 初始化两个角色
        self.first_officer = FirstOfficer(llm_client, socketio, config)
        self.purser = Purser(llm_client, socketio, config)
        
        # 订阅事件
        event_bus.on('crew_message', self.on_crew_message)
        event_bus.on('cabin_crew_request', self.on_crew_request)
        event_bus.on('emergency_alert', self.on_emergency)
        
        print(f"CrewManager: Initialized with FO={self.first_officer.name}, Purser={self.purser.name}")
    
    def on_crew_message(self, data):
        """
        处理机长发给机组的消息。
        target: 'fo' / 'purser' / 'all'
        """
        if not self.enabled:
            return
        
        text = data.get('text', '')
        target = data.get('target', 'all')
        
        # 使用 LLM 生成回复
        if target in ['fo', 'all']:
            self._llm_respond(self.first_officer, text)
        if target in ['purser', 'all']:
            self._llm_respond(self.purser, text)
    
    def on_crew_request(self, request_type):
        """处理按钮触发的请求。"""
        if not self.enabled:
            return
        
        if request_type == 'status':
            self.purser.report_status()
        elif request_type == 'chat':
            self._llm_respond(self.purser, "你好，有什么需要帮忙的吗？")
        elif request_type == 'boarding':
            self.socketio.emit('play_ambience', {'sound': 'boarding_ambience.mp3', 'loop': True})
            self.purser.send_message("Boarding started, Captain. Cabin crew prepare for boarding.")
        elif request_type == 'deboarding':
            self.socketio.emit('play_ambience', {'sound': 'deboarding_ambience.mp3', 'loop': True})
            self.purser.send_message("Deboarding started. Thank you for flying with us.")
        elif request_type == 'stop_ambience':
             self.socketio.emit('stop_ambience')
             self.purser.send_message("Ambience sound stopped.")
    
    def on_emergency(self, data):
        """紧急情况通知机组。"""
        if not self.enabled:
            return
        
        emergency_type = data.get('type', 'unknown')
        self.purser.emergency_alert(emergency_type)
    
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
                    event_bus.emit('tts_request', response.strip())
                    
            except Exception as e:
                print(f"CrewManager: LLM error: {e}")
                if crew_member.role == 'purser':
                    crew_member.report_status()
                else:
                    crew_member.assist_pilot(user_message)
        
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
