"""
ATIS Generator - Generates ATIS broadcast from METAR data.
Issue 7: Create ATIS broadcast, cache until METAR changes.
"""
import hashlib
from datetime import datetime, timezone
from .context import event_bus


class ATISGenerator:
    """Generates and caches ATIS broadcasts from METAR data."""
    
    # Phonetic alphabet for ATIS information letter
    PHONETIC = ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo', 'Foxtrot', 
                'Golf', 'Hotel', 'India', 'Juliet', 'Kilo', 'Lima', 'Mike',
                'November', 'Oscar', 'Papa', 'Quebec', 'Romeo', 'Sierra', 
                'Tango', 'Uniform', 'Victor', 'Whiskey', 'X-ray', 'Yankee', 'Zulu']
    CHINA_AIRPORT_NAMES = {
        # ── 华北 ──────────────────────────────────────────────────────
        'ZBAA': '北京首都国际机场',
        'ZBAD': '北京大兴国际机场',
        'ZBTJ': '天津滨海国际机场',
        'ZBSJ': '石家庄正定国际机场',
        'ZBYN': '太原武宿国际机场',
        'ZBHH': '呼和浩特白塔国际机场',

        # ── 东北 ──────────────────────────────────────────────────────
        'ZYTX': '沈阳桃仙国际机场',
        'ZYTL': '大连周水子国际机场',
        'ZYHB': '哈尔滨太平国际机场',
        'ZYCC': '长春龙嘉国际机场',
        'ZYYY': '沈阳于洪机场',           # 备降场，保留以防误匹配

        # ── 华东 ──────────────────────────────────────────────────────
        'ZSPD': '上海浦东国际机场',
        'ZSSS': '上海虹桥国际机场',
        'ZSHC': '杭州萧山国际机场',
        'ZSNJ': '南京禄口国际机场',
        'ZSNB': '宁波栎社国际机场',
        'ZSWZ': '温州龙湾国际机场',
        'ZSQD': '青岛胶东国际机场',
        'ZSJN': '济南遥墙国际机场',
        'ZSAM': '厦门高崎国际机场',
        'ZSQZ': '泉州晋江国际机场',
        'ZSCN': '南昌昌北国际机场',
        'ZSOF': '合肥新桥国际机场',
        'ZSHF': '合肥骆岗机场',

        # ── 中南 ──────────────────────────────────────────────────────
        'ZGGG': '广州白云国际机场',
        'ZGSZ': '深圳宝安国际机场',
        'ZGHA': '长沙黄花国际机场',
        'ZHHH': '武汉天河国际机场',
        'ZHCC': '郑州新郑国际机场',
        'ZGNN': '南宁吴圩国际机场',
        'ZGKL': '桂林两江国际机场',
        'ZGSD': '揭阳潮汕国际机场',
        'ZGZH': '珠海金湾机场',
        'ZGFS': '佛山沙堤机场',

        # ── 海南 ──────────────────────────────────────────────────────
        'ZJHK': '海口美兰国际机场',
        'ZJSY': '三亚凤凰国际机场',

        # ── 西南 ──────────────────────────────────────────────────────
        'ZUUU': '成都天府国际机场',
        'ZUUL': '成都双流国际机场',
        'ZUCK': '重庆江北国际机场',
        'ZPPP': '昆明长水国际机场',
        'ZUGY': '贵阳龙洞堡国际机场',
        'ZULS': '拉萨贡嘎机场',
        'ZUWX': '重庆万州五桥机场',

        # ── 西北 ──────────────────────────────────────────────────────
        'ZLXY': '西安咸阳国际机场',
        'ZWWW': '乌鲁木齐地窝堡国际机场',
        'ZLIC': '兰州中川国际机场',
        'ZLYC': '银川河东国际机场',
        'ZLXN': '西宁曹家堡机场',

        # ── 港澳台 ────────────────────────────────────────────────────
        'VHHH': '香港国际机场',
        'VMMC': '澳门国际机场',
        'RCTP': '台湾桃园国际机场',
        'RCSS': '台北松山机场',
        'RCKH': '高雄国际机场',
        'RCMQ': '台中清泉岗机场',
        'RCFN': '台东丰年机场',
        'RCKA': '金门尚义机场',
    }
    
    def __init__(self, config, socketio, airport_frequency_service=None):
        self.config = config
        self.socketio = socketio
        self.airport_frequency_service = airport_frequency_service
        self.cached_atis = {}  # {icao: {'hash': ..., 'text': ..., 'letter_idx': ...}}
        self.pending_playback = set()
        self.active_atis_icao = None  # currently looping ATIS airport

        event_bus.on('atis_playback_request', self.on_atis_request)
        event_bus.on('metar_updated', self.on_metar_updated)
        event_bus.on('atis_played', self._on_atis_played_repeat)
        event_bus.on('atis_stop', self._on_atis_stop)
        print("ATISGenerator: Initialized.")

    def _is_china_airport(self, icao):
        code = (icao or "").strip().upper()
        return code.startswith('Z') or code.startswith('VH') or code.startswith('VM') or code.startswith('RC')

    def _emit_atis_log(self, icao, atis_text):
        formatted = atis_text.replace('\n', '<br>')
        event_bus.emit('external_chat_log', f'{icao} ATIS', formatted)

    def _english_airport_name(self, icao, weather_data):
        if self.airport_frequency_service:
            from_csv = self.airport_frequency_service.get_airport_name(icao)
            if from_csv:
                return from_csv
        if weather_data and weather_data.get('name'):
            return weather_data['name'].split(',')[0].replace('/', ' ').strip()
        return (icao or 'Unknown').upper()

    def _chinese_airport_name(self, icao, weather_data=None):
        name = self.CHINA_AIRPORT_NAMES.get((icao or '').upper())
        if name:
            return name
        # Fall back to English name rather than reading the raw ICAO code
        return self._english_airport_name(icao, weather_data)

    def _digits_enunciated_zh(self, text, use_liang=True):
        digit_map = {'0': '洞', '1': '幺', '2': '两', '3': '三', '4': '四', '5': '五', '6': '六', '7': '拐', '8': '八', '9': '九'}
        return ''.join(digit_map.get(ch, ch) for ch in str(text))

    def _digits_display(self, text):
        """Display digits as-is for Chinese text (not phonetic)."""
        return str(text)

    def _format_runway_chinese(self, runway):
        """Return runway designation for display — keep Arabic digits, add Chinese suffix."""
        if not runway:
            return runway
        runway_upper = runway.upper().strip()
        suffix = ''
        if runway_upper.endswith('R'):
            suffix = '右'
            runway_upper = runway_upper[:-1]
        elif runway_upper.endswith('L'):
            suffix = '左'
            runway_upper = runway_upper[:-1]
        elif runway_upper.endswith('C'):
            suffix = '中'
            runway_upper = runway_upper[:-1]
        return runway_upper + suffix  # e.g. "34右" — TTS engine converts digits to phonetics

    def _format_visibility(self, weather_data):
        visib = (weather_data or {}).get('visib', '')

        def _zh_vis(meters: int) -> str:
            """Format visibility in Chinese for TTS (integer km or meters)."""
            if meters >= 9990:
                return "10千米以上"
            if meters >= 1000:
                km = meters / 1000
                # Express as integer km when whole, else one decimal
                if km == int(km):
                    return f"{int(km)}千米"
                return f"{km:.1f}千米".rstrip('0').rstrip('.')  + "千米" if False else f"{km:.1f}千米"
            return f"{meters}米"

        def _sm_to_m(sm_val):
            return int(float(sm_val) * 1609)

        if isinstance(visib, (int, float)):
            meters = _sm_to_m(visib)
            return f"Greater than 10 KM" if meters >= 9990 else f"{meters} meters", _zh_vis(meters)
        visib_text = str(visib).strip()
        if visib_text.endswith('+'):
            # "6+" means >6 statute miles — convert SM→m
            try:
                meters = _sm_to_m(float(visib_text[:-1]))
            except ValueError:
                meters = 9999
            en = "Greater than 10 KM" if meters >= 9990 else f"Greater than {visib_text[:-1]} SM"
            return en, _zh_vis(meters)
        if visib_text.isdigit():
            # Raw integer from API — treat as SM
            meters = _sm_to_m(int(visib_text))
            return f"{meters} meters", _zh_vis(meters)
        return "Visibility unknown", "能见度不详"

    def _format_clouds(self, weather_data):
        clouds_data = (weather_data or {}).get('clouds', [])
        if not clouds_data:
            return "Sky clear", "晴空"
        first = clouds_data[0]
        cover = (first.get('cover') or '').upper()
        base = int(first.get('base') or 0)
        cover_en = {
            'FEW': 'Few',
            'SCT': 'Scattered',
            'BKN': 'Broken',
            'OVC': 'Overcast',
        }.get(cover, cover or 'Clouds')
        cover_zh = {
            'FEW': '少云',
            'SCT': '疏云',
            'BKN': '多云',
            'OVC': '阴',
        }.get(cover, '云')
        if base:
            return f"{cover_en} {base}", f"{self._digits_display(base)}英尺{cover_zh}"
        return cover_en, cover_zh

    def _format_wind(self, weather_data):
        wdir = int((weather_data or {}).get('wdir', 0) or 0)
        wspd = int((weather_data or {}).get('wspd', 0) or 0)
        if wspd <= 0:
            return "Wind calm.", "静风。"
        return f"Wind {wdir:03d} at {wspd}.", f"地面风{wdir:03d}/{wspd:02d}。"

    def _format_wind_zh(self, weather_data, metar_raw=None):
        """中文ATIS风向风速格式：风向XXX度 风速X米秒（无"每"字）+ 风向变化（如有）。"""
        import re
        wdir = int((weather_data or {}).get('wdir', 0) or 0)
        wspd_kt = int((weather_data or {}).get('wspd', 0) or 0)
        wgst_kt = int((weather_data or {}).get('wgst', 0) or 0)

        if wspd_kt <= 0:
            return "静风"

        # 节→米/秒（四舍五入至整数）
        wspd_ms = round(wspd_kt * 0.5144)
        wgst_ms = round(wgst_kt * 0.5144) if wgst_kt > 0 else 0

        # 风向显示用阿拉伯数字，TTS引擎负责转换为无线电读法
        if metar_raw and re.search(r'\bVRB\d', metar_raw):
            wdir_str = "不定"
        elif wdir == 0:
            wdir_str = "360"
        else:
            wdir_str = f"{wdir:03d}"

        result = f"风向{wdir_str}度 风速{wspd_ms}米秒"

        if wgst_ms > 0:
            result += f" 阵风{wgst_ms}米秒"

        if metar_raw:
            var_match = re.search(r'\b(\d{3})V(\d{3})\b', metar_raw)
            if var_match:
                result += f"\n风向变化{var_match.group(1)}度到{var_match.group(2)}度"

        return result

    def _format_clouds_zh(self, weather_data):
        """中文ATIS云层格式（显示用数字，TTS转换朗读）。"""
        clouds_data = (weather_data or {}).get('clouds', [])
        if not clouds_data:
            return "晴空"

        cover_map = {'FEW': '少云', 'SCT': '疏云', 'BKN': '多云', 'OVC': '阴'}
        parts = []
        for cloud in clouds_data[:3]:
            cover = (cloud.get('cover') or '').upper()
            base_ft = int(cloud.get('base') or 0)
            cover_zh = cover_map.get(cover, '云')
            base_m = int(round(base_ft * 0.3048 / 100.0)) * 100
            if base_m > 0:
                parts.append(f"{cover_zh} 云底高度{base_m}米")
            else:
                parts.append(cover_zh)
        return ' '.join(parts)

    def _format_visibility_zh(self, weather_data):
        """中文ATIS能见度（显示用数字，TTS转换朗读）。"""
        visib = (weather_data or {}).get('visib', '')
        def _meters(raw) -> int:
            if isinstance(raw, (int, float)):
                return int(float(raw) * 1609)
            s = str(raw).strip()
            if s.endswith('+'):
                try:
                    return int(float(s[:-1]) * 1609)
                except ValueError:
                    return 9999
            if s.isdigit():
                return int(float(s) * 1609)
            return 9999
        m = _meters(visib)
        if m >= 9990:
            return "能见度10千米或以上"
        if m >= 1000:
            km = m / 1000
            if km == int(km):
                return f"能见度{int(km)}千米"
            # 非整数：取整到最近百米
            m_rounded = int(round(m / 100)) * 100
            km_part = m_rounded // 1000
            bai_part = (m_rounded % 1000) // 100
            if bai_part:
                return f"能见度{km_part}千{bai_part}百米"
            return f"能见度{km_part}千米"
        return f"能见度{m}米"

    def _format_temp_dew_zh(self, weather_data):
        """中文ATIS气温露点（显示用数字，负温加"零下"前缀）。"""
        temp_c = (weather_data or {}).get('temp', None)
        dewp_c = (weather_data or {}).get('dewp', None)
        if temp_c is None:
            return "气温不详"
        def _fmt(v):
            i = int(v)
            return f"零下{abs(i)}" if i < 0 else str(i)
        result = f"气温{_fmt(temp_c)}"
        if dewp_c is not None:
            result += f" 露点{_fmt(dewp_c)}"
        return result

    def _format_qnh_zh(self, weather_data):
        """中文ATIS修正海压（显示用数字）。"""
        altim = (weather_data or {}).get('altim', None)
        if altim is None:
            return "修正海压不详"
        return f"修正海压 {int(float(altim))}"

    def _format_time_zh(self, hhmm_str):
        """中文ATIS协调时（显示用数字）。"""
        return f"协调时 {hhmm_str}"

    # 过渡高度 / 过渡高度层（单位：米）— 按ICAO前缀分区
    # 大多数中国机场：TA 3000 m / TL 3600 m（海拔低于1500m的机场）
    # 高原机场（昆明/成都/拉萨等）：TA 4500–6000 m，这里保守取3000 m通用值
    _TRANSITION_TABLE = {
        # 标准平原机场 TA=3000m TL=3600m
        'DEFAULT':  (3000, 3600),
        # 高原/高高原机场 TA更高（暂用通用值，实际应查NOTAMs）
        'ZPPP': (4500, 5100),   # 昆明
        'ZUUU': (4200, 4800),   # 成都天府
        'ZUUL': (4200, 4800),   # 成都双流
        'ZULS': (6000, 6600),   # 拉萨
        'ZUGY': (4200, 4800),   # 贵阳
    }

    def _transition_altitudes_zh(self, icao):
        ta, tl = self._TRANSITION_TABLE.get((icao or '').upper(), self._TRANSITION_TABLE['DEFAULT'])

        def _fmt_m(m):
            """格式化米数（阿拉伯数字+中文单位，例：3000→3千米，3600→3千6米）。"""
            if m % 1000 == 0:
                return f"{m // 1000}千米"
            q, r = divmod(m, 1000)
            bai = r // 100
            if r % 100 == 0:
                return f"{q}千{bai}米"
            return f"{q}千{bai}百米"

        return f"过渡高度 {_fmt_m(ta)}\n过渡高度层 {_fmt_m(tl)}"

    def _format_temp_dew(self, weather_data):
        temp_c = (weather_data or {}).get('temp', None)
        dewp_c = (weather_data or {}).get('dewp', None)
        temp_en = str(int(temp_c)) if temp_c is not None else "Unknown"
        dew_en = str(int(dewp_c)) if dewp_c is not None else "Unknown"
        temp_zh = self._digits_display(int(temp_c)) if temp_c is not None else "不详"
        dew_zh = self._digits_display(int(dewp_c)) if dewp_c is not None else "不详"
        return f"Temperature {temp_en}, dew point {dew_en}.", f"温度{temp_zh}，露点{dew_zh}。"

    def _format_qnh(self, weather_data):
        altim = (weather_data or {}).get('altim', None)
        if altim is None:
            return "QNH unknown.", "QNH不详。"
        qnh = str(int(float(altim)))
        return f"QNH {qnh}.", f"QNH {qnh}。"

    def _format_runway(self, icao, weather_data):
        if not self.airport_frequency_service:
            return "", ""
        wind_dir = (weather_data or {}).get('wdir', None)
        runways = self.airport_frequency_service.get_preferred_runways(icao, wind_dir=wind_dir, limit=2)
        if not runways:
            return "", ""
        runway_text_en = ' and '.join(runways)   # English: "34R and 34L" — no slash
        primary = runways[0]
        # Chinese display: "34右 / 34左" — TTS replaces "/" with "和" before synthesis
        runways_zh = ' / '.join([self._format_runway_chinese(r) for r in runways])
        primary_zh = self._format_runway_chinese(primary)
        if len(runways) == 1:
            return f"ILS runway {primary} approach in use.", f"使用跑道{primary_zh}。"
        return f"ILS runway {primary} approach in use. Parallel runways {runway_text_en}.", f"使用跑道{runways_zh}。"

    def _format_report_time(self, weather_data, metar_raw):
        report_time = None
        if weather_data:
            report_time = weather_data.get('reportTime') or weather_data.get('receiptTime')
            if not report_time and weather_data.get('obsTime'):
                try:
                    report_time = datetime.fromtimestamp(
                        int(weather_data['obsTime']),
                        tz=timezone.utc
                    ).isoformat()
                except Exception:
                    report_time = None

        if report_time:
            try:
                normalized = report_time.replace('Z', '+00:00')
                dt = datetime.fromisoformat(normalized).astimezone(timezone.utc)
                hhmm = dt.strftime('%H%M')
                return f"{hhmm} Zulu", f"{hhmm} 世界时"
            except Exception:
                pass

        tokens = (metar_raw or '').split()
        for token in tokens:
            if len(token) == 7 and token.endswith('Z') and token[:-1].isdigit():
                hhmm = token[2:6]
                return f"{hhmm} Zulu", f"{hhmm} 世界时"

        return "Unknown", "不详"
    
    def _parse_metar_to_atis(self, icao, metar_raw, weather_data=None):
        """Convert METAR to spoken ATIS format."""
        # Get or increment information letter
        if icao not in self.cached_atis:
            # Start at a time-based letter so it's not always Alpha on every app launch
            initial_idx = datetime.now(timezone.utc).hour % 26
            self.cached_atis[icao] = {'hash': '', 'text': '', 'letter_idx': initial_idx}

        info_letter = self.PHONETIC[self.cached_atis[icao]['letter_idx'] % 26]
        
        report_time_en = "Unknown"
        report_time_zh = "不详"
        
        if weather_data:
            report_time_en, report_time_zh = self._format_report_time(weather_data, metar_raw)
        airport_name_en = self._english_airport_name(icao, weather_data)
        airport_name_zh = self._chinese_airport_name(icao, weather_data)
        visibility_en, visibility_zh = self._format_visibility(weather_data)
        clouds_en, clouds_zh = self._format_clouds(weather_data)
        wind_en, wind_zh = self._format_wind(weather_data)
        temp_en, temp_zh = self._format_temp_dew(weather_data)
        qnh_en, qnh_zh = self._format_qnh(weather_data)
        runway_en, runway_zh = self._format_runway(icao, weather_data)
        
        english_atis = f"""
{airport_name_en} automatic terminal information service Information {info_letter}.
{report_time_en}.
{wind_en}
Visibility {visibility_en}.
{clouds_en}.
{temp_en}
{qnh_en}
{runway_en}
Advise on initial contact you have Information {info_letter}.
""".strip()

        if not self._is_china_airport(icao):
            return english_atis

        # ── 中文ATIS（民航标准格式）──────────────────────────────────────
        # 时间（协调时 逐字无线电）
        hhmm_raw = report_time_en.split()[0] if report_time_en != "Unknown" else ""
        time_zh   = self._format_time_zh(hhmm_raw) if hhmm_raw else "协调时不详"

        # 跑道（逐字无线电）
        _, runway_zh_new = self._format_runway(icao, weather_data)
        # 仅保留跑道号部分，去掉"使用"前缀与句号
        runway_line = f"跑道{runway_zh_new.replace('使用跑道', '').rstrip('。')}" if runway_zh_new else ""

        # 风（米秒，逐字无线电）
        wind_zh_new = self._format_wind_zh(weather_data, metar_raw)

        # 能见度
        vis_zh_new = self._format_visibility_zh(weather_data)

        # 云
        clouds_zh_new = self._format_clouds_zh(weather_data)

        # 气温露点
        temp_zh_new = self._format_temp_dew_zh(weather_data)

        # 修正海压
        qnh_zh_new  = self._format_qnh_zh(weather_data)

        # 过渡高度/过渡高度层
        trans_zh = self._transition_altitudes_zh(icao)

        lines = [
            f"{airport_name_zh}通播{info_letter}",
            time_zh,
        ]
        if runway_line:
            lines.append(runway_line)
        lines += [
            wind_zh_new,
            vis_zh_new,
            clouds_zh_new,
            temp_zh_new,
            qnh_zh_new,
            trans_zh,
        ]
        chinese_atis = '\n'.join(lines)

        return f"{english_atis}\n\n{chinese_atis}"
    
    def on_metar_updated(self, icao, metar_raw, weather_data):
        """Called when METAR is updated. Regenerate ATIS if changed."""
        metar_hash = hashlib.md5(metar_raw.encode()).hexdigest()
        
        if icao in self.cached_atis and self.cached_atis[icao]['hash'] == metar_hash:
            # METAR unchanged, use cached ATIS
            print(f"ATISGenerator: METAR unchanged for {icao}, using cached ATIS.")
            if icao in self.pending_playback:
                self.pending_playback.discard(icao)
                self._emit_atis_log(icao, self.cached_atis[icao]['text'])
                event_bus.emit('atis_tts_request', self.cached_atis[icao]['text'], icao)
            return
        
        # METAR changed - regenerate ATIS
        print(f"ATISGenerator: Generating new ATIS for {icao}...")
        
        # Initialize or increment letter
        if icao not in self.cached_atis:
            # First time - use time-based initial letter
            initial_idx = datetime.now(timezone.utc).hour % 26
            self.cached_atis[icao] = {'hash': '', 'text': '', 'letter_idx': initial_idx}
        else:
            # Increment letter for METAR change
            self.cached_atis[icao]['letter_idx'] = (self.cached_atis[icao]['letter_idx'] + 1) % 26
        
        atis_text = self._parse_metar_to_atis(icao, metar_raw, weather_data)
        
        self.cached_atis[icao]['hash'] = metar_hash
        self.cached_atis[icao]['text'] = atis_text
        
        print(f"ATISGenerator: New ATIS for {icao}: Information {self.PHONETIC[self.cached_atis[icao]['letter_idx']]}")
        if icao in self.pending_playback:
            self.pending_playback.discard(icao)
            self._emit_atis_log(icao, atis_text)
            event_bus.emit('atis_tts_request', atis_text, icao)
        elif self.active_atis_icao == icao:
            # ATIS updated while currently looping — show new log entry and restart with updated text
            self._emit_atis_log(icao, atis_text)
            event_bus.emit('atis_tts_request', atis_text, icao)
    
    def on_atis_request(self, icao):
        """Play ATIS for the given airport."""
        if not icao or icao == 'N/A':
            print("ATISGenerator: No valid ICAO for ATIS request.")
            return

        self.active_atis_icao = icao

        if icao in self.cached_atis and self.cached_atis[icao]['text']:
            atis_text = self.cached_atis[icao]['text']
            print(f"ATISGenerator: Playing cached ATIS for {icao}")
            self._emit_atis_log(icao, atis_text)
            event_bus.emit('atis_tts_request', atis_text, icao)
            # Always refresh METAR in background — new data will update on next loop cycle
            event_bus.emit('metar_fetch_request', icao)
        else:
            # No cached ATIS - request METAR first
            print(f"ATISGenerator: No cached ATIS for {icao}. Fetching METAR...")
            self.pending_playback.add(icao)
            event_bus.emit('metar_fetch_request', icao)

    def _on_atis_played_repeat(self, icao):
        """After ATIS finishes playing, loop it if still on this ATIS frequency."""
        if self.active_atis_icao != icao:
            return
        if icao not in self.cached_atis or not self.cached_atis[icao]['text']:
            return
        print(f"ATISGenerator: Looping ATIS for {icao}")
        # Re-emit TTS only (no chat log entry for repeats)
        event_bus.emit('atis_tts_request', self.cached_atis[icao]['text'], icao)

    def _on_atis_stop(self):
        """Stop ATIS looping (called when frequency changes away from ATIS)."""
        if self.active_atis_icao:
            print(f"ATISGenerator: Stopping ATIS loop for {self.active_atis_icao}")
            self.active_atis_icao = None
