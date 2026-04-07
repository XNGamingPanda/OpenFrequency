"""
Career Mode - Job Generator
生成航线任务，根据玩家等级和所在机场提供可用任务
"""
import time
import random
import math
from urllib.parse import urlencode
from typing import List, Dict, Any

from ..aircraft_catalog import AircraftCatalog

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

    SIMBRIEF_TYPE_MAP = {
        'B738': 'B738',
        'A320': 'A320',
        'B77W': 'B77W',
        'A350': 'A359',
        'B748': 'B748',
        'A380': 'A388',
        'C172': 'C172',
        'PA28': 'P28A',
        'C208': 'C208',
        'BE58': 'BE58',
        'TBM9': 'TBM9',
        'CRJ7': 'CRJ7',
        'E175': 'E75L',
    }

    AIRLINE_POOLS = {
        'CN': {
            'passenger': ['CCA', 'CES', 'CSN', 'CHH', 'CXA', 'HXA', 'CDG', 'CBJ', 'CSH', 'GCR', 'DKH', 'LKE', 'CUA', 'CQH', 'JYH', 'OTC'],
            'cargo': ['CKK', 'CYZ', 'CAO', 'CSS', 'YZR'],
            'charter': ['BJN', 'DKH', 'GCR', 'CUA']
        },
        'HK': {
            'passenger': ['CPA', 'CRK', 'HKE', 'AHK'],
            'cargo': ['CPA', 'AHK'],
            'charter': ['HKE', 'BJN']
        },
        'MO': {
            'passenger': ['AMU'],
            'cargo': ['AMU'],
            'charter': ['AMU']
        },
        'TW': {
            'passenger': ['CAL', 'EVA', 'UIA', 'TTW'],
            'cargo': ['CAL', 'EVA'],
            'charter': ['EVA', 'UIA']
        },
        'JP': {
            'passenger': ['ANA', 'JAL', 'JTA', 'SKY', 'SFJ', 'ADO', 'APJ', 'SJO', 'FDA', 'IBX', 'JJP', 'WAJ', 'AKX'],
            'cargo': ['NCA', 'ANA', 'JAL'],
            'charter': ['JAL', 'ANA', 'SKY', 'FDA']
        },
        'KR': {
            'passenger': ['KAL', 'AAR', 'JJA', 'ABL', 'TWB', 'ESR', 'ASV'],
            'cargo': ['KAL', 'AAR'],
            'charter': ['JJA', 'AAR']
        },
        'SG': {
            'passenger': ['SIA', 'SCO', 'JSA'],
            'cargo': ['SIA'],
            'charter': ['SIA', 'SCO']
        },
        'TH': {
            'passenger': ['THA', 'AIQ', 'NOK', 'TVJ'],
            'cargo': ['THA'],
            'charter': ['AIQ', 'NOK']
        },
        'MY': {
            'passenger': ['MAS', 'AXM', 'MXD'],
            'cargo': ['MAS'],
            'charter': ['AXM', 'MXD']
        },
        'ID': {
            'passenger': ['GIA', 'LNI', 'BTK', 'CTV'],
            'cargo': ['GIA'],
            'charter': ['LNI', 'BTK']
        },
        'PH': {
            'passenger': ['PAL', 'CEB', 'APG'],
            'cargo': ['PAL'],
            'charter': ['CEB', 'APG']
        },
        'VN': {
            'passenger': ['HVN', 'VJC', 'BAV'],
            'cargo': ['HVN'],
            'charter': ['VJC', 'BAV']
        },
        'US': {
            'passenger': ['AAL', 'DAL', 'UAL', 'SWA', 'ASA', 'JBU', 'FFT', 'NKS', 'HAL', 'SKW', 'ENY', 'RPA'],
            'cargo': ['FDX', 'UPS', 'GTI', 'ABX', 'ATN', 'NCR'],
            'charter': ['EJA', 'XSR', 'JBU', 'LXJ']
        },
        'GB': {
            'passenger': ['BAW', 'EZY', 'VIR', 'RYR', 'LOG', 'BEE'],
            'cargo': ['DHK', 'BAW'],
            'charter': ['VIR', 'BAW']
        },
        'DE': {
            'passenger': ['DLH', 'EWG', 'CFG', 'TUI', 'GWI'],
            'cargo': ['GEC', 'BCS'],
            'charter': ['CFG', 'TUI']
        },
        'FR': {
            'passenger': ['AFR', 'TVF', 'CRL', 'HOP'],
            'cargo': ['AFR'],
            'charter': ['TVF', 'CRL']
        },
        'AE': {
            'passenger': ['UAE', 'ETD', 'FDB', 'ABY'],
            'cargo': ['UAE', 'ETD'],
            'charter': ['UAE', 'ETD']
        },
        'AU': {
            'passenger': ['QFA', 'VOZ', 'JST', 'RXA'],
            'cargo': ['QFA'],
            'charter': ['QFA', 'VOZ']
        },
        'NZ': {
            'passenger': ['ANZ', 'JST'],
            'cargo': ['ANZ'],
            'charter': ['ANZ']
        },
        'CA': {
            'passenger': ['ACA', 'WJA', 'JZA', 'TSC', 'ROU'],
            'cargo': ['ACA'],
            'charter': ['TSC', 'ROU']
        },
        'DEFAULT': {
            'passenger': ['AAL', 'DAL', 'UAL', 'BAW', 'DLH', 'AFR'],
            'cargo': ['FDX', 'UPS', 'GTI'],
            'charter': ['EJA', 'EXC', 'JET']
        }
    }
    
    def __init__(self, career_profile, airport_service=None, config=None):
        self.career_profile = career_profile
        self.airport_service = airport_service
        self.config = config or {}
        self.aircraft_catalog = AircraftCatalog(self.config)
        self._rng = random.SystemRandom()

    AIRLINE_NAMES = {
        'CCA': 'Air China', 'CES': 'China Eastern', 'CSN': 'China Southern', 'CHH': 'Hainan Airlines',
        'CXA': 'XiamenAir', 'HXA': 'China Express', 'CDG': 'Shandong Airlines', 'CBJ': 'Beijing Capital Airlines',
        'CSH': 'Shanghai Airlines', 'GCR': 'Tianjin Airlines', 'DKH': 'Juneyao Air', 'LKE': 'Lucky Air',
        'CUA': 'China United Airlines', 'CQH': 'Spring Airlines', 'JYH': '9 Air', 'OTC': 'Colorful Guizhou Airlines',
        'CKK': 'China Cargo Airlines', 'CYZ': 'China Postal Airlines', 'CAO': 'Air China Cargo', 'CSS': 'SF Airlines',
        'CPA': 'Cathay Pacific', 'CRK': 'Hong Kong Airlines', 'HKE': 'Hong Kong Express', 'AHK': 'Air Hong Kong',
        'CAL': 'China Airlines', 'EVA': 'EVA Air', 'UIA': 'Uni Air', 'TTW': 'Tigerair Taiwan',
        'ANA': 'All Nippon Airways', 'JAL': 'Japan Airlines', 'JTA': 'Japan Transocean Air', 'SKY': 'Skymark Airlines',
        'SFJ': 'StarFlyer', 'ADO': 'Air Do', 'APJ': 'Peach Aviation', 'SJO': 'Spring Japan', 'FDA': 'Fuji Dream Airlines',
        'IBX': 'IBEX Airlines', 'JJP': 'Jetstar Japan', 'WAJ': 'Air Japan', 'AKX': 'Amakusa Airlines', 'NCA': 'Nippon Cargo Airlines',
        'KAL': 'Korean Air', 'AAR': 'Asiana Airlines', 'JJA': 'Jeju Air', 'ABL': 'Air Busan', 'TWB': 'Tway Air',
        'ESR': 'Eastar Jet', 'ASV': 'Air Seoul',
        'SIA': 'Singapore Airlines', 'SCO': 'Scoot', 'JSA': 'Jetstar Asia',
        'THA': 'Thai Airways', 'AIQ': 'Thai AirAsia', 'NOK': 'Nok Air', 'TVJ': 'Thai VietJet',
        'MAS': 'Malaysia Airlines', 'AXM': 'AirAsia', 'MXD': 'Batik Air Malaysia',
        'GIA': 'Garuda Indonesia', 'LNI': 'Lion Air', 'BTK': 'Batik Air', 'CTV': 'Citilink',
        'PAL': 'Philippine Airlines', 'CEB': 'Cebu Pacific', 'APG': 'Air Philippines',
        'HVN': 'Vietnam Airlines', 'VJC': 'VietJet Air', 'BAV': 'Bamboo Airways',
        'AAL': 'American Airlines', 'DAL': 'Delta Air Lines', 'UAL': 'United Airlines', 'SWA': 'Southwest Airlines',
        'ASA': 'Alaska Airlines', 'JBU': 'JetBlue', 'FFT': 'Frontier Airlines', 'NKS': 'Spirit Airlines',
        'FDX': 'FedEx', 'UPS': 'UPS Airlines', 'GTI': 'Atlas Air',
        'BAW': 'British Airways', 'EZY': 'easyJet', 'VIR': 'Virgin Atlantic', 'RYR': 'Ryanair',
        'DLH': 'Lufthansa', 'EWG': 'Eurowings', 'CFG': 'Condor', 'TUI': 'TUI fly',
        'AFR': 'Air France', 'TVF': 'Transavia France', 'CRL': 'Corsair',
        'UAE': 'Emirates', 'ETD': 'Etihad Airways', 'FDB': 'flydubai', 'ABY': 'Air Arabia',
        'QFA': 'Qantas', 'VOZ': 'Virgin Australia', 'JST': 'Jetstar', 'ANZ': 'Air New Zealand',
        'ACA': 'Air Canada', 'WJA': 'WestJet', 'JZA': 'Jazz Aviation', 'TSC': 'Air Transat'
    }

    def _airport_info(self, icao: str) -> Dict[str, Any]:
        icao = (icao or "").upper()
        if icao in self.AIRPORTS:
            return self.AIRPORTS[icao]
        if self.airport_service:
            airport = None
            if hasattr(self.airport_service, 'get_airport_position'):
                airport = self.airport_service.get_airport_position(icao)
            if airport:
                return {
                    'name': airport.get('name') or icao,
                    'lat': airport.get('lat') or airport.get('latitude_deg'),
                    'lon': airport.get('lon') or airport.get('longitude_deg'),
                    'size': 2,
                    'country': airport.get('iso_country', ''),
                    'type': airport.get('type', ''),
                    'scheduled_service': airport.get('scheduled_service', ''),
                }
        return {}

    def _rank_index(self, profile: Dict[str, Any]) -> int:
        rank = profile.get('rank', 'Student (P0)')
        code = ''
        if '(' in rank and ')' in rank:
            code = rank.split('(')[-1].split(')')[0]
        for idx, (_, rank_code, _) in enumerate(getattr(self.career_profile, 'RANKS', [])):
            if code == rank_code:
                return idx
        return int(profile.get('rank_index', 0) or 0)
    
    def get_distance_km(self, icao1: str, icao2: str) -> float:
        """Calculate great circle distance between two airports."""
        ap1 = self._airport_info(icao1)
        ap2 = self._airport_info(icao2)
        if not ap1 or not ap2 or ap1.get('lat') is None or ap2.get('lat') is None:
            return 0
        
        lat1, lon1 = math.radians(ap1['lat']), math.radians(ap1['lon'])
        lat2, lon2 = math.radians(ap2['lat']), math.radians(ap2['lon'])
        
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))
        
        return 6371 * c  # km

    def _candidate_airports(self):
        candidates = dict(self.AIRPORTS)
        # Keep the generator bounded and fast even when backed by OurAirports.
        if self.airport_service and hasattr(self.airport_service, '_airport_positions'):
            with self.airport_service._lock:
                airport_positions = dict(self.airport_service._airport_positions)
            for ident, airport in airport_positions.items():
                ident = (ident or '').upper()
                if len(ident) != 4 or ident in candidates:
                    continue
                lat = airport.get('lat') or airport.get('latitude_deg')
                lon = airport.get('lon') or airport.get('longitude_deg')
                if lat is None or lon is None:
                    continue
                name = (airport.get('name') or ident).strip()
                airport_type = (airport.get('type') or '').lower()
                scheduled = (airport.get('scheduled_service') or '').lower()
                if airport_type and airport_type not in {'small_airport', 'medium_airport', 'large_airport'}:
                    continue
                if airport_type == 'small_airport' and scheduled != 'yes':
                    continue
                if any(token in name.lower() for token in ('heliport', 'helipad', 'seaplane', 'balloonport')):
                    continue
                candidates[ident] = {
                    'name': name,
                    'lat': float(lat),
                    'lon': float(lon),
                    'size': 2,
                    'country': airport.get('iso_country', ''),
                    'type': airport_type,
                    'scheduled_service': scheduled
                }
        return candidates

    def _country_for_airport(self, icao: str) -> str:
        info = self._airport_info(icao)
        country = (info.get('country') or '').upper()
        if country:
            return country

        icao = (icao or '').upper()
        prefix_map = {
            'ZB': 'CN', 'ZG': 'CN', 'ZH': 'CN', 'ZJ': 'CN', 'ZL': 'CN', 'ZP': 'CN',
            'ZS': 'CN', 'ZU': 'CN', 'ZW': 'CN', 'ZY': 'CN',
            'VH': 'HK', 'VM': 'MO', 'RC': 'TW', 'RJ': 'JP', 'RO': 'JP', 'RK': 'KR',
            'WS': 'SG', 'VT': 'TH', 'WM': 'MY', 'WI': 'ID', 'WA': 'ID', 'RP': 'PH',
            'VV': 'VN', 'K': 'US', 'P': 'US'
        }
        for prefix, mapped_country in prefix_map.items():
            if icao.startswith(prefix):
                return mapped_country
        return 'DEFAULT'

    def available_airlines(self, origin: str = '') -> List[Dict[str, Any]]:
        """Return plausible airlines the pilot can sign with at this airport."""
        country = self._country_for_airport(origin)
        pools = self.AIRLINE_POOLS.get(country) or self.AIRLINE_POOLS['DEFAULT']
        codes = []
        for category in ('passenger', 'cargo', 'charter'):
            for code in pools.get(category, []):
                if code not in codes:
                    codes.append(code)
        return [
            {
                'code': code,
                'name': self.AIRLINE_NAMES.get(code, code),
                'country': country,
                'category': self._airline_category(code, pools)
            }
            for code in codes
        ]

    def _airline_category(self, code: str, pools: Dict[str, List[str]]) -> str:
        for category in ('passenger', 'cargo', 'charter'):
            if code in pools.get(category, []):
                return category
        return 'passenger'

    def _current_airline_code(self, profile: Dict[str, Any], job_type: str, origin: str, destination: str) -> str:
        airline = profile.get('current_airline') or {}
        code = (airline.get('code') or '').upper()
        if not code:
            return ''
        country = airline.get('country') or self._country_for_airport(origin)
        pools = self.AIRLINE_POOLS.get(country) or self.AIRLINE_POOLS['DEFAULT']
        valid_codes = set(pools.get(job_type, [])) | set(pools.get('passenger', [])) | set(pools.get('cargo', [])) | set(pools.get('charter', []))
        if code in valid_codes:
            return code
        return code

    def _allowed_job_types_for_airline(self, profile: Dict[str, Any], origin: str, distance: float) -> List[str]:
        """Constrain mission types so employer and job label do not conflict."""
        airline = profile.get('current_airline') or {}
        code = (airline.get('code') or '').upper()
        country = airline.get('country') or self._country_for_airport(origin)
        pools = self.AIRLINE_POOLS.get(country) or self.AIRLINE_POOLS['DEFAULT']

        if code in pools.get('cargo', []) and code not in pools.get('passenger', []):
            return ['cargo']
        if code in pools.get('charter', []) and code not in pools.get('passenger', []) and code not in pools.get('cargo', []):
            return ['charter']

        if distance < 500:
            return ['passenger']
        if distance < 1500:
            return ['passenger', 'charter'] if code in pools.get('charter', []) else ['passenger']
        return ['passenger', 'charter'] if code in pools.get('charter', []) else ['passenger']

    def _flight_number_from_callsign(self, callsign: str, airline_code: str) -> str:
        callsign = (callsign or '').upper()
        airline_code = (airline_code or '').upper()
        if airline_code and callsign.startswith(airline_code):
            number = callsign[len(airline_code):]
            return number or str(self._rng.randint(100, 9999))
        digits = ''.join(ch for ch in callsign if ch.isdigit())
        return digits or str(self._rng.randint(100, 9999))

    def _simbrief_url(self, job: Dict[str, Any]) -> str:
        airline_code = (job.get('airline_code') or job.get('callsign', '')[:3] or '').upper()
        aircraft = job.get('aircraft') or 'B738'
        params = {
            'orig': job.get('origin'),
            'dest': job.get('destination'),
            'type': self.SIMBRIEF_TYPE_MAP.get(aircraft, aircraft),
            'airline': airline_code,
            'fltnum': self._flight_number_from_callsign(job.get('callsign', ''), airline_code),
            'callsign': job.get('callsign'),
            'flighttype': 'c' if job.get('type') == 'cargo' else 's',
            'flightrules': 'i',
            'route': job.get('route', ''),
        }
        if job.get('cruise_alt'):
            params['fl'] = str(job.get('cruise_alt'))
        return f"https://dispatch.simbrief.com/options/custom?{urlencode({k: v for k, v in params.items() if v not in (None, '')})}"

    def build_simbrief_url(self, job: Dict[str, Any]) -> str:
        """Build a SimBrief dispatch redirect URL for an existing job."""
        return self._simbrief_url(job)

    def _generate_callsign(self, job_type: str, origin: str = '', destination: str = '', airline_code: str = '') -> str:
        """Generate a region-aware realistic callsign for the job."""
        if airline_code:
            prefix = airline_code.upper()
            number = self._rng.randint(100, 9999)
            return f"{prefix}{number}"

        origin_country = self._country_for_airport(origin)
        dest_country = self._country_for_airport(destination)
        country = origin_country if origin_country != 'DEFAULT' else dest_country
        pools = self.AIRLINE_POOLS.get(country) or self.AIRLINE_POOLS['DEFAULT']
        prefixes = pools.get(job_type) or pools.get('passenger') or self.AIRLINE_POOLS['DEFAULT']['passenger']
        prefix = self._rng.choice(prefixes)
        number = self._rng.randint(100, 9999)
        return f"{prefix}{number}"
    
    def generate_jobs(self, current_airport: str, count: int = 8) -> List[Dict[str, Any]]:
        """Generate available flight jobs from current airport."""
        profile = self.career_profile.get_profile()
        if not profile.get('current_airline'):
            return []
        current_airport = (current_airport or 'ZBAA').upper()
        if not self._airport_info(current_airport):
            current_airport = 'ZBAA'
        rank_index = self._rank_index(profile)
        
        jobs = []
        
        # Calculate distances and filter destinations based on rank
        destinations_with_distance = []
        airports = self._candidate_airports()
        for icao in airports.keys():
            if icao != current_airport:
                distance = self.get_distance_km(current_airport, icao)
                if distance:
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
        
        if not valid_destinations:
            return []

        # Weighted random: short routes are favored for low ranks, but the list is
        # still refreshed each time so the market does not look fixed.
        weighted = []
        for icao, distance in valid_destinations:
            weight = max(1, int(max_dist - distance + 100)) if rank_index <= 1 else max(1, int(distance))
            weighted.append((icao, distance, weight))

        selected = []
        pool = weighted[:]
        while pool and len(selected) < count:
            total = sum(item[2] for item in pool)
            pick = self._rng.uniform(0, total)
            upto = 0
            for idx, item in enumerate(pool):
                upto += item[2]
                if upto >= pick:
                    selected.append((item[0], item[1]))
                    pool.pop(idx)
                    break
        
        # Generate jobs for selected destinations
        for dest, distance in selected:
            
            job_types = self._allowed_job_types_for_airline(profile, current_airport, distance)
            job_type = self._rng.choice(job_types)
            airline_code = self._current_airline_code(profile, job_type, current_airport, dest)
            
            # Calculate pay
            base_pay = distance * self.PAY_RATES[job_type]
            rank_bonus = 1 + (rank_index * 0.1)  # 10% bonus per rank
            pay = int(base_pay * rank_bonus)
            
            # Determine aircraft
            max_rank = min(rank_index, 5)
            aircraft_pool = self.aircraft_catalog.allowed_for_rank(max_rank, self.AIRCRAFT_BY_RANK)
            aircraft = self._rng.choice(aircraft_pool)
            
            # XP reward
            xp = int(distance * 0.5)  # 0.5 XP per km
            
            job = {
                'id': f"{current_airport}-{dest}-{self._rng.randint(1000, 9999)}",
                'origin': current_airport,
                'origin_name': self._airport_info(current_airport).get('name', current_airport),
                'destination': dest,
                'destination_name': self._airport_info(dest).get('name', dest),
                'distance_km': round(distance),
                'type': job_type,
                'aircraft': aircraft,
                'pay': pay,
                'xp_reward': xp,
                'callsign': self._generate_callsign(job_type, current_airport, dest, airline_code),
                'airline_code': airline_code,
                'airline_name': self.AIRLINE_NAMES.get(airline_code, airline_code),
                'airline_region': self._country_for_airport(current_airport),
                'route': '',
                'route_source': 'simbrief_recommended',
                'generated_at': int(time.time()),
            }
            job['simbrief_url'] = self._simbrief_url(job)
            jobs.append(job)
        
        # Sort by distance
        jobs.sort(key=lambda x: x['distance_km'])
        return jobs
    
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
