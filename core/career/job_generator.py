"""
Career Mode - Job Generator
生成航线任务，根据玩家等级和所在机场提供可用任务
"""
import random
import math
from typing import List, Dict, Any

class JobGenerator:
    """Generate flight jobs/missions for career mode."""
    
    # Sample airport data (ICAO: {name, lat, lon, size})
    # Size: 1=small, 2=medium, 3=large
    AIRPORTS = {
        # China - Regional (for PPL short routes)
        'ZBTJ': {'name': 'Tianjin Binhai', 'lat': 39.12, 'lon': 117.35, 'size': 2, 'country': 'CN'},  # 120km from Beijing
        'ZBSJ': {'name': 'Shijiazhuang', 'lat': 38.28, 'lon': 114.70, 'size': 2, 'country': 'CN'},  # 280km from Beijing
        'ZBYN': {'name': 'Taiyuan Wusu', 'lat': 37.75, 'lon': 112.63, 'size': 2, 'country': 'CN'},  # 400km from Beijing
        'ZHHD': {'name': 'Handan', 'lat': 36.52, 'lon': 114.43, 'size': 1, 'country': 'CN'},  # 380km from Beijing
        'ZBDT': {'name': 'Datong Yungang', 'lat': 40.06, 'lon': 113.48, 'size': 1, 'country': 'CN'},  # 280km from Beijing
        'ZBCD': {'name': 'Chengde Puning', 'lat': 41.12, 'lon': 118.07, 'size': 1, 'country': 'CN'},  # 180km from Beijing
        'ZBQD': {'name': 'Qinhuangdao Beidaihe', 'lat': 39.67, 'lon': 119.73, 'size': 1, 'country': 'CN'},  # 280km from Beijing
        
        # China - Major
        'ZBAA': {'name': 'Beijing Capital', 'lat': 40.08, 'lon': 116.58, 'size': 3, 'country': 'CN'},
        'ZSPD': {'name': 'Shanghai Pudong', 'lat': 31.14, 'lon': 121.80, 'size': 3, 'country': 'CN'},
        'ZGGG': {'name': 'Guangzhou Baiyun', 'lat': 23.39, 'lon': 113.30, 'size': 3, 'country': 'CN'},
        'ZGSZ': {'name': 'Shenzhen Bao\'an', 'lat': 22.64, 'lon': 113.81, 'size': 3, 'country': 'CN'},
        'ZUUU': {'name': 'Chengdu Shuangliu', 'lat': 30.58, 'lon': 103.95, 'size': 3, 'country': 'CN'},
        'ZLXY': {'name': 'Xi\'an Xianyang', 'lat': 34.44, 'lon': 108.75, 'size': 2, 'country': 'CN'},
        'ZSHC': {'name': 'Hangzhou Xiaoshan', 'lat': 30.23, 'lon': 120.43, 'size': 2, 'country': 'CN'},
        'ZUCK': {'name': 'Chongqing Jiangbei', 'lat': 29.72, 'lon': 106.64, 'size': 2, 'country': 'CN'},
        'ZWWW': {'name': 'Urumqi Diwopu', 'lat': 43.91, 'lon': 87.47, 'size': 2, 'country': 'CN'},
        'ZSSS': {'name': 'Shanghai Hongqiao', 'lat': 31.20, 'lon': 121.33, 'size': 2, 'country': 'CN'},
        
        # International
        'VHHH': {'name': 'Hong Kong Intl', 'lat': 22.31, 'lon': 113.91, 'size': 3, 'country': 'HK'},
        'RJTT': {'name': 'Tokyo Haneda', 'lat': 35.55, 'lon': 139.78, 'size': 3, 'country': 'JP'},
        'RKSI': {'name': 'Seoul Incheon', 'lat': 37.46, 'lon': 126.44, 'size': 3, 'country': 'KR'},
        'WSSS': {'name': 'Singapore Changi', 'lat': 1.36, 'lon': 103.99, 'size': 3, 'country': 'SG'},
        'VTBS': {'name': 'Bangkok Suvarnabhumi', 'lat': 13.69, 'lon': 100.75, 'size': 3, 'country': 'TH'},
    }
    
    # Aircraft types by rank requirement
    AIRCRAFT_BY_RANK = {
        0: ['C172', 'PA28'],           # Student
        1: ['C208', 'BE58', 'TBM9'],   # PPL
        2: ['CRJ7', 'E175', 'B738'],   # CPL
        3: ['A320', 'B738', 'B77W'],   # ATPL
        4: ['A350', 'B77W', 'B748'],   # Senior Captain
        5: ['A380', 'B748', 'B77W'],   # Master Aviator
    }
    
    # Pay rates (per km)
    PAY_RATES = {
        'cargo': 2.5,
        'passenger': 3.0,
        'charter': 4.0,
        'emergency': 5.0,
    }
    
    def __init__(self, career_profile):
        self.career_profile = career_profile
    
    def get_distance_km(self, icao1: str, icao2: str) -> float:
        """Calculate great circle distance between two airports."""
        if icao1 not in self.AIRPORTS or icao2 not in self.AIRPORTS:
            return 0
        
        ap1 = self.AIRPORTS[icao1]
        ap2 = self.AIRPORTS[icao2]
        
        lat1, lon1 = math.radians(ap1['lat']), math.radians(ap1['lon'])
        lat2, lon2 = math.radians(ap2['lat']), math.radians(ap2['lon'])
        
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))
        
        return 6371 * c  # km
    
    def generate_jobs(self, current_airport: str, count: int = 8) -> List[Dict[str, Any]]:
        """Generate available flight jobs from current airport."""
        profile = self.career_profile.get_profile()
        rank_index = profile.get('rank_index', 0)
        
        jobs = []
        
        # Calculate distances and filter destinations based on rank
        destinations_with_distance = []
        for icao in self.AIRPORTS.keys():
            if icao != current_airport:
                distance = self.get_distance_km(current_airport, icao)
                destinations_with_distance.append((icao, distance))
        
        # Filter by rank (max distance allowed)
        max_distance_by_rank = {
            0: 500,   # Student (P0) - short regional flights only
            1: 1000,  # PPL - medium domestic flights
            2: 2000,  # CPL - long domestic / short international
            3: 4000,  # ATPL - international
            4: 6000,  # Senior Captain - long haul
            5: 10000, # Master Aviator - any distance
        }
        max_dist = max_distance_by_rank.get(rank_index, 10000)
        
        # Filter valid destinations
        valid_destinations = [(icao, d) for icao, d in destinations_with_distance 
                              if 100 < d <= max_dist]  # Min 100km, max by rank
        
        # Sort by distance and pick random subset
        valid_destinations.sort(key=lambda x: x[1])
        if len(valid_destinations) > count:
            # For low rank, prefer shorter routes
            if rank_index <= 1:
                selected = valid_destinations[:count]  # Take shortest routes
            else:
                selected = random.sample(valid_destinations, count)
        else:
            selected = valid_destinations
        
        # Generate jobs for selected destinations
        for dest, distance in selected:
            
            # Determine job type based on distance and rank
            if distance < 500:
                job_types = ['cargo', 'passenger']
            elif distance < 1500:
                job_types = ['cargo', 'passenger', 'charter']
            else:
                job_types = ['passenger', 'charter']
            
            job_type = random.choice(job_types)
            
            # Calculate pay
            base_pay = distance * self.PAY_RATES[job_type]
            rank_bonus = 1 + (rank_index * 0.1)  # 10% bonus per rank
            pay = int(base_pay * rank_bonus)
            
            # Determine aircraft
            max_rank = min(rank_index, 5)
            aircraft_pool = self.AIRCRAFT_BY_RANK.get(max_rank, ['C172'])
            aircraft = random.choice(aircraft_pool)
            
            # XP reward
            xp = int(distance * 0.5)  # 0.5 XP per km
            
            job = {
                'id': f"{current_airport}-{dest}-{random.randint(1000, 9999)}",
                'origin': current_airport,
                'origin_name': self.AIRPORTS[current_airport]['name'],
                'destination': dest,
                'destination_name': self.AIRPORTS[dest]['name'],
                'distance_km': round(distance),
                'type': job_type,
                'aircraft': aircraft,
                'pay': pay,
                'xp_reward': xp,
                'callsign': self._generate_callsign(job_type),
            }
            jobs.append(job)
        
        # Sort by distance
        jobs.sort(key=lambda x: x['distance_km'])
        return jobs
    
    def _generate_callsign(self, job_type: str) -> str:
        """Generate a realistic callsign for the job."""
        if job_type == 'cargo':
            prefixes = ['CKK', 'SQC', 'CAO', 'HU']
        elif job_type == 'charter':
            prefixes = ['JET', 'EXC', 'VIP']
        else:
            prefixes = ['CCA', 'CES', 'CSN', 'CHH', 'CXA', 'HXA']
        
        prefix = random.choice(prefixes)
        number = random.randint(100, 9999)
        return f"{prefix}{number}"
    
    def accept_job(self, job: Dict[str, Any]) -> bool:
        """Accept a job and lock in the callsign."""
        if not job:
            return False
        
        # Store active job
        with self.career_profile.lock:
            self.career_profile.profile['active_job'] = job
            self.career_profile._save_profile()
        
        return True
    
    def complete_job(self, job_id: str, landing_score: str = 'C') -> Dict[str, Any]:
        """Complete a job and award rewards."""
        profile = self.career_profile.get_profile()
        active_job = profile.get('active_job')
        
        if not active_job or active_job.get('id') != job_id:
            return {'success': False, 'error': 'No matching active job'}
        
        # Calculate rewards with landing bonus
        landing_multipliers = {'S': 1.5, 'A': 1.3, 'B': 1.1, 'C': 1.0, 'D': 0.8, 'F': 0.5}
        multiplier = landing_multipliers.get(landing_score, 1.0)
        
        final_pay = int(active_job['pay'] * multiplier)
        final_xp = int(active_job['xp_reward'] * multiplier)
        
        # Award rewards
        self.career_profile.add_money(final_pay)
        self.career_profile.add_xp(final_xp)
        
        # Clear active job
        with self.career_profile.lock:
            self.career_profile.profile['active_job'] = None
            self.career_profile.profile.setdefault('completed_jobs', 0)
            self.career_profile.profile['completed_jobs'] += 1
            self.career_profile._save_profile()
        
        return {
            'success': True,
            'pay': final_pay,
            'xp': final_xp,
            'landing_score': landing_score,
            'multiplier': multiplier,
        }
