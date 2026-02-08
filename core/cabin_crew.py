"""
Cabin Crew LLM Module
乘务组通信模块 - 支持紧急情况主动联系、日常陪聊
"""
import threading
import random
from .context import shared_context, context_lock, event_bus

class CabinCrew:
    """
    乘务组通信模块
    - 紧急情况主动联系机长
    - 日常陪聊/休闲对话
    - 独立按键触发
    """
    
    CABIN_CREW_NAMES = [
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
    
    def __init__(self, config, llm_client, socketio):
        self.config = config
        self.llm_client = llm_client
        self.socketio = socketio
        self.crew_name = random.choice(self.CABIN_CREW_NAMES)
        self.enabled = config.get('cabin_crew', {}).get('enabled', True)
        
        # 订阅事件
        event_bus.on('cabin_crew_request', self.on_crew_request)
        event_bus.on('emergency_alert', self.on_emergency)
        event_bus.on('user_cabin_message', self.on_user_message)
        
        print(f"CabinCrew: Initialized with crew member '{self.crew_name}'")
    
    def on_crew_request(self, request_type):
        """处理乘务组请求（按钮触发）"""
        if not self.enabled:
            return
        
        if request_type == 'status':
            # 状态报告
            msg = random.choice(self.IDLE_MESSAGES)
            self._send_to_pilot(msg)
        elif request_type == 'chat':
            # 陪聊请求 - 使用 LLM
            self._llm_chat("你好，有什么需要帮忙的吗？")
    
    def on_emergency(self, data):
        """紧急情况主动联系机长"""
        if not self.enabled:
            return
        
        emergency_type = data.get('type', 'unknown')
        
        # 根据紧急类型选择消息
        if emergency_type == 'medical':
            msg = random.choice([m for m in self.EMERGENCY_ALERTS if 'medical' in m.lower() or '心脏' in m or '晕倒' in m])
        elif emergency_type == 'fire':
            msg = random.choice([m for m in self.EMERGENCY_ALERTS if 'fire' in m.lower() or '火' in m or 'smoke' in m.lower()])
        else:
            msg = random.choice(self.EMERGENCY_ALERTS)
        
        self._send_to_pilot(msg, urgent=True)
        
        # 触发 TTS
        event_bus.emit('tts_request', msg)
    
    def on_user_message(self, text):
        """处理机长发送的消息"""
        if not self.enabled:
            return
        
        # 使用 LLM 生成回复
        self._llm_chat(text)
    
    def _send_to_pilot(self, message, urgent=False):
        """发送消息到驾驶舱"""
        sender = f"Cabin ({self.crew_name})"
        
        self.socketio.emit('chat_log', {
            'sender': sender,
            'text': message,
            'urgent': urgent
        })
        
        if urgent:
            # 紧急消息播放警告音
            self.socketio.emit('play_warning_sound', {'type': 'cabin_emergency'})
    
    def _llm_chat(self, user_message):
        """使用 LLM 生成乘务组回复"""
        
        def _generate():
            try:
                with context_lock:
                    altitude = shared_context['aircraft'].get('altitude', 0)
                    phase = "taxiing" if altitude < 100 else "cruising" if altitude > 10000 else "climbing/descending"
                
                # 构建乘务组专用 Prompt
                system_prompt = f"""
                You are {self.crew_name}, a friendly and professional cabin crew member on this flight.
                Current flight phase: {phase}
                Your role:
                - Be helpful, warm, and professional
                - You can discuss cabin-related topics (passengers, service, safety)
                - Keep responses brief and conversational
                - If the pilot seems stressed, offer encouragement
                - You can engage in light chat to keep the pilot company on long flights
                
                Reply in the SAME LANGUAGE as the pilot's message.
                Keep responses under 50 words.
                """
                
                # 调用 LLM
                response = self.llm_client._call_llm_sync(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    max_tokens=100
                )
                
                if response:
                    self._send_to_pilot(response.strip())
                    event_bus.emit('tts_request', response.strip())
                    
            except Exception as e:
                print(f"CabinCrew: LLM error: {e}")
                fallback = random.choice(self.IDLE_MESSAGES)
                self._send_to_pilot(fallback)
        
        # 异步执行
        threading.Thread(target=_generate, daemon=True).start()
    
    def trigger_random_event(self):
        """触发随机乘务组事件（用于压力测试）"""
        if not self.enabled:
            return
        
        if random.random() < 0.1:  # 10% 概率紧急事件
            self.on_emergency({'type': random.choice(['medical', 'fire', 'other'])})
        else:
            self.on_crew_request('status')
