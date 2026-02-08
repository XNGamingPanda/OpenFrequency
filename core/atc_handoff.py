"""
ATC Handoff State Machine
管制移交状态机 - 实现完整的 ATC 移交流程

移交顺序:
ATIS抄收 → 放行 → 地面/机坪 → 塔台起飞 → 离场 → 中心 → 进场 → 塔台降落 → 地面/机坪
"""
import threading
from enum import Enum, auto
from .context import shared_context, context_lock, event_bus

class ATCPhase(Enum):
    """ATC 阶段枚举"""
    ATIS = auto()         # 抄收 ATIS
    CLEARANCE = auto()    # 放行
    GROUND_DEP = auto()   # 地面/机坪 (出发)
    TOWER_DEP = auto()    # 塔台 (起飞)
    DEPARTURE = auto()    # 离场
    CENTER = auto()       # 中心/区调
    APPROACH = auto()     # 进场
    TOWER_ARR = auto()    # 塔台 (降落)
    GROUND_ARR = auto()   # 地面/机坪 (到达)
    PARKED = auto()       # 完成停机

class ATCHandoffManager:
    """
    ATC 移交状态机管理器
    - 自动检测当前飞行阶段
    - 强制移交到正确的管制单位
    - 自动触发 ATIS 抄收
    """
    
    # 每个阶段对应的管制频率范围 (MHz)
    FREQ_RANGES = {
        ATCPhase.ATIS: (126.0, 128.0),
        ATCPhase.CLEARANCE: (121.0, 122.0),
        ATCPhase.GROUND_DEP: (121.5, 122.0),
        ATCPhase.TOWER_DEP: (118.0, 120.0),
        ATCPhase.DEPARTURE: (119.0, 125.0),
        ATCPhase.CENTER: (128.0, 136.0),
        ATCPhase.APPROACH: (124.0, 127.0),
        ATCPhase.TOWER_ARR: (118.0, 120.0),
        ATCPhase.GROUND_ARR: (121.5, 122.0),
    }
    
    # 阶段转换条件
    TRANSITION_CONDITIONS = {
        ATCPhase.ATIS: {'next': ATCPhase.CLEARANCE, 'condition': 'atis_copied'},
        ATCPhase.CLEARANCE: {'next': ATCPhase.GROUND_DEP, 'condition': 'clearance_received'},
        ATCPhase.GROUND_DEP: {'next': ATCPhase.TOWER_DEP, 'condition': 'holding_short'},
        ATCPhase.TOWER_DEP: {'next': ATCPhase.DEPARTURE, 'condition': 'airborne'},
        ATCPhase.DEPARTURE: {'next': ATCPhase.CENTER, 'condition': 'cruise_altitude'},
        ATCPhase.CENTER: {'next': ATCPhase.APPROACH, 'condition': 'descending'},
        ATCPhase.APPROACH: {'next': ATCPhase.TOWER_ARR, 'condition': 'final_approach'},
        ATCPhase.TOWER_ARR: {'next': ATCPhase.GROUND_ARR, 'condition': 'landed'},
        ATCPhase.GROUND_ARR: {'next': ATCPhase.PARKED, 'condition': 'parked'},
    }
    
    def __init__(self, config, socketio):
        self.config = config
        self.socketio = socketio
        self.current_phase = ATCPhase.ATIS
        self.atis_copied = False
        self.clearance_received = False
        self.last_phase = None
        
        # 航班特定数据
        self.origin_icao = None
        self.dest_icao = None
        self.cruise_altitude = 0
        
        # 订阅事件
        event_bus.on('telemetry_update', self.on_telemetry)
        event_bus.on('flight_plan_loaded', self.on_flight_plan)
        event_bus.on('atis_played', self.on_atis_played)
        event_bus.on('clearance_confirmed', self.on_clearance_confirmed)
        event_bus.on('handoff_complete', self.on_handoff_complete)
        
        print("ATCHandoffManager: Initialized")
    
    def on_flight_plan(self, flight_plan):
        """航班计划加载时初始化"""
        self.origin_icao = flight_plan.get('origin')
        self.dest_icao = flight_plan.get('destination')
        self.cruise_altitude = int(flight_plan.get('cruise_alt', 0))
        
        # 自动请求 ATIS
        if self.origin_icao:
            print(f"ATCHandoffManager: 自动获取 {self.origin_icao} ATIS...")
            self._request_atis(self.origin_icao)
            self._broadcast_phase_change()
    
    def on_telemetry(self, data):
        """根据遥测数据检测阶段转换"""
        alt = data.get('altitude', 0)
        gs = data.get('groundspeed', 0)
        vs = data.get('vs', 0)
        on_ground = data.get('on_ground', True)
        
        old_phase = self.current_phase
        
        # 阶段自动检测
        if self.current_phase == ATCPhase.ATIS:
            # 等待 ATIS 被抄收
            if self.atis_copied:
                self._transition_to(ATCPhase.CLEARANCE)
        
        elif self.current_phase == ATCPhase.CLEARANCE:
            if self.clearance_received:
                self._transition_to(ATCPhase.GROUND_DEP)
        
        elif self.current_phase == ATCPhase.GROUND_DEP:
            # 如果正在滑行且速度 > 5 且在地面
            if on_ground and gs > 5:
                # 检测是否在跑道等待
                pass  # 需要更多逻辑来检测 holding short
        
        elif self.current_phase == ATCPhase.TOWER_DEP:
            # 离地后移交离场
            if not on_ground and alt > 500:
                self._transition_to(ATCPhase.DEPARTURE)
        
        elif self.current_phase == ATCPhase.DEPARTURE:
            # 到达巡航高度移交中心
            if alt > 18000 and abs(vs) < 500:
                self._transition_to(ATCPhase.CENTER)
        
        elif self.current_phase == ATCPhase.CENTER:
            # 开始下降移交进场
            if vs < -300 and alt < self.cruise_altitude * 0.8:
                self._transition_to(ATCPhase.APPROACH)
        
        elif self.current_phase == ATCPhase.APPROACH:
            # 进入五边移交塔台
            if alt < 3000 and not on_ground:
                self._transition_to(ATCPhase.TOWER_ARR)
        
        elif self.current_phase == ATCPhase.TOWER_ARR:
            # 落地后移交地面
            if on_ground and gs < 80:
                self._transition_to(ATCPhase.GROUND_ARR)
        
        elif self.current_phase == ATCPhase.GROUND_ARR:
            # 停机
            if on_ground and gs < 1:
                self._transition_to(ATCPhase.PARKED)
        
        # 广播阶段变化
        if old_phase != self.current_phase:
            self._broadcast_phase_change()
    
    def _transition_to(self, new_phase):
        """执行阶段转换"""
        old_phase = self.current_phase
        self.current_phase = new_phase
        
        phase_names = {
            ATCPhase.ATIS: "ATIS",
            ATCPhase.CLEARANCE: "Clearance Delivery",
            ATCPhase.GROUND_DEP: "Ground",
            ATCPhase.TOWER_DEP: "Tower",
            ATCPhase.DEPARTURE: "Departure",
            ATCPhase.CENTER: "Center",
            ATCPhase.APPROACH: "Approach",
            ATCPhase.TOWER_ARR: "Tower",
            ATCPhase.GROUND_ARR: "Ground",
            ATCPhase.PARKED: "Parked"
        }
        
        print(f"ATCHandoffManager: 阶段转换 {phase_names[old_phase]} → {phase_names[new_phase]}")
        
        # 触发主动移交事件
        event_bus.emit('mandatory_handoff', {
            'from_phase': old_phase.name,
            'to_phase': new_phase.name,
            'controller': phase_names[new_phase]
        })
        
        # 如果是进场阶段，自动获取目的地 ATIS
        if new_phase == ATCPhase.APPROACH and self.dest_icao:
            print(f"ATCHandoffManager: 自动获取 {self.dest_icao} ATIS...")
            self._request_atis(self.dest_icao)
    
    def _request_atis(self, icao):
        """请求 ATIS 广播"""
        event_bus.emit('atis_playback_request', icao)
    
    def _broadcast_phase_change(self):
        """广播当前阶段到前端"""
        self.socketio.emit('atc_phase_update', {
            'phase': self.current_phase.name,
            'origin': self.origin_icao,
            'destination': self.dest_icao
        })
    
    def on_atis_played(self, icao):
        """ATIS 播放完成"""
        self.atis_copied = True
        print(f"ATCHandoffManager: ATIS {icao} 已抄收")
    
    def on_clearance_confirmed(self):
        """放行确认"""
        self.clearance_received = True
        print("ATCHandoffManager: 放行已确认")
    
    def on_handoff_complete(self, data):
        """处理手动移交完成"""
        target_phase = data.get('phase')
        if target_phase:
            try:
                new_phase = ATCPhase[target_phase]
                self._transition_to(new_phase)
            except KeyError:
                print(f"ATCHandoffManager: Unknown phase {target_phase}")
    
    def reset(self):
        """重置状态（新航班）"""
        self.current_phase = ATCPhase.ATIS
        self.atis_copied = False
        self.clearance_received = False
        self.origin_icao = None
        self.dest_icao = None
        self.cruise_altitude = 0
        print("ATCHandoffManager: 状态已重置")
    
    def get_current_controller(self):
        """获取当前应该联系的管制单位"""
        controller_map = {
            ATCPhase.ATIS: "ATIS",
            ATCPhase.CLEARANCE: "Clearance Delivery",
            ATCPhase.GROUND_DEP: f"{self.origin_icao or 'Airport'} Ground",
            ATCPhase.TOWER_DEP: f"{self.origin_icao or 'Airport'} Tower",
            ATCPhase.DEPARTURE: "Departure Control",
            ATCPhase.CENTER: "Center Control",
            ATCPhase.APPROACH: f"{self.dest_icao or 'Airport'} Approach",
            ATCPhase.TOWER_ARR: f"{self.dest_icao or 'Airport'} Tower",
            ATCPhase.GROUND_ARR: f"{self.dest_icao or 'Airport'} Ground",
            ATCPhase.PARKED: "Parked"
        }
        return controller_map.get(self.current_phase, "Unknown")
    
    def get_suggested_frequency(self):
        """获取当前阶段建议的频率"""
        freq_range = self.FREQ_RANGES.get(self.current_phase)
        if freq_range:
            # 返回范围中点作为建议
            return (freq_range[0] + freq_range[1]) / 2
        return 121.5  # 默认紧急

    def manual_advance(self):
        """手动推进到下一阶段（调试用）"""
        transition = self.TRANSITION_CONDITIONS.get(self.current_phase)
        if transition:
            self._transition_to(transition['next'])
