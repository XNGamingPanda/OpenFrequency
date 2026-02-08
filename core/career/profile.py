"""
Career Mode - Profile Management
管理用户生涯档案：XP、等级、飞行时间、违规记录等
"""
import os
import json
import threading
from datetime import datetime

class CareerProfile:
    """管理用户生涯档案"""
    
    RANKS = [
        ("Student", "P0", 0),
        ("Private Pilot", "PPL", 500),
        ("Commercial Pilot", "CPL", 2000),
        ("Airline Pilot", "ATPL", 5000),
        ("Senior Captain", "SCP", 10000),
        ("Master Aviator", "MA", 25000)
    ]
    
    DEFAULT_PROFILE = {
        "callsign": "STUDENT01",
        "rank": "Student (P0)",
        "xp": 0,
        "money": 5000,
        "total_flight_time": 0.0,  # 小时
        "violations": [],
        "licenses": ["P0"],
        "flights_completed": 0,
        "landings": 0,
        "best_landing_g": None,
        "created_at": None,
        "last_flight": None
    }
    
    def __init__(self, data_dir="data/career"):
        self.data_dir = data_dir
        self.profile_path = os.path.join(data_dir, "profile.json")
        self.profile = None
        self.lock = threading.Lock()
        
        os.makedirs(data_dir, exist_ok=True)
        self._load_profile()
        
        print(f"CareerProfile: Loaded - {self.profile['callsign']} ({self.profile['rank']})")
    
    def _load_profile(self):
        """加载或创建档案"""
        if os.path.exists(self.profile_path):
            try:
                with open(self.profile_path, 'r', encoding='utf-8') as f:
                    self.profile = json.load(f)
                # 确保所有字段存在
                for key, val in self.DEFAULT_PROFILE.items():
                    if key not in self.profile:
                        self.profile[key] = val
            except Exception as e:
                print(f"CareerProfile: Error loading profile: {e}")
                self._create_default_profile()
        else:
            self._create_default_profile()
    
    def _create_default_profile(self):
        """创建默认档案"""
        self.profile = self.DEFAULT_PROFILE.copy()
        self.profile['created_at'] = datetime.now().isoformat()
        self._save_profile()
        print("CareerProfile: Created new profile")
    
    def _save_profile(self):
        """保存档案到磁盘"""
        with self.lock:
            try:
                with open(self.profile_path, 'w', encoding='utf-8') as f:
                    json.dump(self.profile, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"CareerProfile: Error saving profile: {e}")
    
    def get_profile(self) -> dict:
        """获取档案副本"""
        with self.lock:
            return self.profile.copy()
    
    def update_callsign(self, callsign: str):
        """更新呼号"""
        with self.lock:
            self.profile['callsign'] = callsign
        self._save_profile()
    
    def add_xp(self, amount: int, reason: str = ""):
        """增加经验值"""
        with self.lock:
            self.profile['xp'] += amount
            if self.profile['xp'] < 0:
                self.profile['xp'] = 0
            
            # 检查升级
            self._check_rank_up()
            
        self._save_profile()
        print(f"CareerProfile: XP +{amount} ({reason}). Total: {self.profile['xp']}")
        return self.profile['xp']
    
    def deduct_xp(self, amount: int, reason: str = ""):
        """扣除经验值"""
        return self.add_xp(-amount, reason)
    
    def add_violation(self, violation_type: str, details: str = ""):
        """记录违规"""
        with self.lock:
            violation = {
                "type": violation_type,
                "details": details,
                "timestamp": datetime.now().isoformat()
            }
            self.profile['violations'].append(violation)
            # 只保留最近50条
            if len(self.profile['violations']) > 50:
                self.profile['violations'] = self.profile['violations'][-50:]
        
        self._save_profile()
        print(f"CareerProfile: Violation recorded - {violation_type}")
    
    def record_flight(self, duration_hours: float, landing_g: float = None):
        """记录航班完成"""
        with self.lock:
            self.profile['flights_completed'] += 1
            self.profile['total_flight_time'] += duration_hours
            self.profile['landings'] += 1
            self.profile['last_flight'] = datetime.now().isoformat()
            
            if landing_g is not None:
                if self.profile['best_landing_g'] is None or landing_g < self.profile['best_landing_g']:
                    self.profile['best_landing_g'] = landing_g
        
        self._save_profile()
    
    def add_money(self, amount: int, reason: str = ""):
        """增加/减少金钱"""
        with self.lock:
            self.profile['money'] += amount
            if self.profile['money'] < 0:
                self.profile['money'] = 0
        
        self._save_profile()
        print(f"CareerProfile: Money {'+' if amount >= 0 else ''}{amount} ({reason})")
    
    def _check_rank_up(self):
        """检查是否升级"""
        current_xp = self.profile['xp']
        new_rank = None
        
        for rank_name, rank_code, threshold in self.RANKS:
            if current_xp >= threshold:
                new_rank = f"{rank_name} ({rank_code})"
        
        if new_rank and new_rank != self.profile['rank']:
            old_rank = self.profile['rank']
            self.profile['rank'] = new_rank
            
            # 添加新执照
            for rank_name, rank_code, threshold in self.RANKS:
                if current_xp >= threshold and rank_code not in self.profile['licenses']:
                    self.profile['licenses'].append(rank_code)
            
            print(f"CareerProfile: 🎉 RANK UP! {old_rank} -> {new_rank}")
            return True
        
        return False
    
    def get_next_rank_progress(self) -> dict:
        """获取下一等级进度"""
        current_xp = self.profile['xp']
        
        for i, (rank_name, rank_code, threshold) in enumerate(self.RANKS):
            if current_xp < threshold:
                prev_threshold = self.RANKS[i-1][2] if i > 0 else 0
                progress = (current_xp - prev_threshold) / (threshold - prev_threshold)
                return {
                    "current_xp": current_xp,
                    "next_rank": f"{rank_name} ({rank_code})",
                    "xp_needed": threshold,
                    "progress": min(progress, 1.0)
                }
        
        # 已达最高等级
        return {
            "current_xp": current_xp,
            "next_rank": None,
            "xp_needed": 0,
            "progress": 1.0
        }
