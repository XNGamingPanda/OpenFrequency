"""
Career Mode - Real-time Flight Evaluator
实时监控飞行参数并评分，检测违规行为
"""
import threading
import time
from datetime import datetime
from ..context import event_bus, shared_context, context_lock

class CareerEvaluator:
    """实时评估飞行质量的后台线程"""
    
    # 评分规则
    RULES = {
        'unstable_approach': {
            'description': '不稳定进近',
            'xp_penalty': 20,
            'condition': '1000ft以下俯仰变化>10度/秒'
        },
        'speed_violation': {
            'description': '低空超速',
            'xp_penalty': 50,
            'condition': '10000ft以下速度>250节'
        },
        'hard_landing': {
            'description': '硬着陆',
            'xp_penalty': 30,
            'condition': '着陆G力>1.8'
        },
        'go_around': {
            'description': '复飞',
            'xp_bonus': 10,
            'condition': '低空加速爬升'
        }
    }
    
    # 着陆奖励
    LANDING_BONUSES = {
        'butter': {'max_g': 1.2, 'xp': 100, 'money': 500},
        'smooth': {'max_g': 1.4, 'xp': 50, 'money': 200},
        'normal': {'max_g': 1.6, 'xp': 20, 'money': 100},
        'firm': {'max_g': 1.8, 'xp': 0, 'money': 50},
        'hard': {'max_g': 2.5, 'xp': -30, 'money': 0}
    }
    
    def __init__(self, config, career_profile, socketio):
        self.config = config
        self.profile = career_profile
        self.socketio = socketio
        self.enabled = config.get('career', {}).get('enabled', False)
        
        # 状态跟踪
        self.flight_active = False
        self.flight_start_time = None
        self.last_pitch = 0
        self.last_check_time = 0
        self.violations_this_flight = []
        
        # 线程控制
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        
        # 订阅事件
        event_bus.on('telemetry_update', self.on_telemetry)
        event_bus.on('landing_detected', self.on_landing)
        event_bus.on('flight_started', self.on_flight_start)
        event_bus.on('flight_ended', self.on_flight_end)
        
        print(f"CareerEvaluator: Initialized (Enabled: {self.enabled})")
    
    def start(self):
        """Start the background thread."""
        if not self._thread.is_alive():
            self._thread.start()
            print("CareerEvaluator: Background thread started")

    def set_mode(self, enabled: bool):
        """Enable/Disable career mode at runtime."""
        self.enabled = enabled
        print(f"CareerEvaluator: Mode set to {'ENABLED' if enabled else 'DISABLED'}")
        
        # Reset state if disabled
        if not enabled:
            self.flight_active = False
            self.violations_this_flight = []
    
    def stop(self):
        self._stop_event.set()
    
    def _loop(self):
        """后台监控循环 (2Hz)"""
        while not self._stop_event.is_set():
            if self.flight_active:
                self._check_violations()
            time.sleep(0.5)
    
    def on_flight_start(self, data):
        """航班开始"""
        if not self.enabled:
            return
        
        self.flight_active = True
        self.flight_start_time = time.time()
        self.violations_this_flight = []
        print("CareerEvaluator: 🛫 Flight started - Evaluation active")
        
        self.socketio.emit('career_event', {
            'type': 'flight_start',
            'message': '✈️ 生涯模式：航班开始计分'
        })
    
    def on_flight_end(self, data):
        """航班结束"""
        if not self.enabled or not self.flight_active:
            return
        
        self.flight_active = False
        flight_duration = (time.time() - self.flight_start_time) / 3600 if self.flight_start_time else 0
        
        # 基础 XP
        base_xp = 100
        
        # 违规扣分已经在实时处理中完成
        
        # 飞行时长奖励
        time_bonus = int(flight_duration * 50)  # 每小时 50 XP
        
        total_xp = base_xp + time_bonus
        self.profile.add_xp(total_xp, f"航班完成 ({flight_duration:.1f}h)")
        self.profile.record_flight(flight_duration)
        
        print(f"CareerEvaluator: 🛬 Flight ended - Duration: {flight_duration:.2f}h, XP: +{total_xp}")
        
        self.socketio.emit('career_event', {
            'type': 'flight_end',
            'message': f'🎉 航班结束！获得 {total_xp} XP',
            'violations': len(self.violations_this_flight),
            'duration': flight_duration
        })
    
    def on_landing(self, data):
        """着陆评估"""
        if not self.enabled:
            return
        
        g_force = data.get('g_force', 1.5)
        
        # 确定着陆等级
        grade = 'hard'
        for grade_name, grade_data in self.LANDING_BONUSES.items():
            if g_force <= grade_data['max_g']:
                grade = grade_name
                break
        
        bonus = self.LANDING_BONUSES[grade]
        xp = bonus['xp']
        money = bonus['money']
        
        if xp != 0:
            self.profile.add_xp(xp, f"着陆 ({grade})")
        if money > 0:
            self.profile.add_money(money, f"着陆奖励 ({grade})")
        
        print(f"CareerEvaluator: Landing grade: {grade.upper()}, G: {g_force:.2f}, XP: {xp}, Money: {money}")
        
        self.socketio.emit('career_event', {
            'type': 'landing',
            'grade': grade,
            'g_force': g_force,
            'xp': xp,
            'money': money
        })
    
    def on_telemetry(self, data):
        """处理遥测数据"""
        if not self.enabled or not self.flight_active:
            return
        
        # 更新状态用于连续检测
        ac = data.get('aircraft', {})
        self.last_pitch = ac.get('pitch', 0)
    
    def _check_violations(self):
        """检查实时违规"""
        now = time.time()
        if now - self.last_check_time < 1.0:  # 每秒检查一次
            return
        self.last_check_time = now
        
        with context_lock:
            ac = shared_context.get('aircraft', {})
            altitude = ac.get('altitude', 0)
            airspeed = ac.get('airspeed', 0)
            pitch = ac.get('pitch', 0)
            on_ground = ac.get('on_ground', True)
        
        # 低空超速检测 (10000ft以下 > 250节)
        if not on_ground and altitude < 10000 and airspeed > 250:
            self._trigger_violation('speed_violation', f"速度: {airspeed:.0f}节 @ {altitude:.0f}ft")
        
        # 不稳定进近检测 (1000ft以下，俯仰变化过大)
        if not on_ground and altitude < 1000 and altitude > 100:
            pitch_rate = abs(pitch - self.last_pitch) * 2  # 简化计算 (0.5s间隔)
            if pitch_rate > 10:
                self._trigger_violation('unstable_approach', f"俯仰变化: {pitch_rate:.1f}度/秒")
    
    def _trigger_violation(self, violation_type: str, details: str):
        """触发违规"""
        # 防止重复触发同一类型
        recent = [v for v in self.violations_this_flight 
                  if v['type'] == violation_type and time.time() - v['time'] < 30]
        if recent:
            return
        
        rule = self.RULES.get(violation_type, {})
        xp_penalty = rule.get('xp_penalty', 10)
        description = rule.get('description', violation_type)
        
        self.profile.deduct_xp(xp_penalty, f"违规: {description}")
        self.profile.add_violation(violation_type, details)
        
        self.violations_this_flight.append({
            'type': violation_type,
            'time': time.time(),
            'details': details
        })
        
        print(f"CareerEvaluator: ⚠️ VIOLATION: {description} - XP -{xp_penalty}")
        
        self.socketio.emit('career_event', {
            'type': 'violation',
            'violation': description,
            'details': details,
            'xp_penalty': xp_penalty
        })
        
        # 触发 ATC 警告
        if violation_type == 'speed_violation':
            event_bus.emit('proactive_atc_request', 'speed_violation_warning', shared_context)
