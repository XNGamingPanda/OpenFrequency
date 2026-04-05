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
        'ZSHC': '杭州萧山国际机场',
    }
    ENGLISH_AIRPORT_NAMES = {
        'ZSHC': 'Hangzhou Xiaoshan International Airport',
    }
    
    def __init__(self, config, socketio, airport_frequency_service=None):
        self.config = config
        self.socketio = socketio
        self.airport_frequency_service = airport_frequency_service
        self.cached_atis = {}  # {icao: {'hash': ..., 'text': ..., 'letter_idx': ...}}
        self.pending_playback = set()
        
        event_bus.on('atis_playback_request', self.on_atis_request)
        event_bus.on('metar_updated', self.on_metar_updated)
        print("ATISGenerator: Initialized.")

    def _is_china_airport(self, icao):
        return (icao or "").strip().upper().startswith('Z')

    def _emit_atis_log(self, icao, atis_text):
        formatted = atis_text.replace('\n', '<br>')
        event_bus.emit('external_chat_log', f'{icao} ATIS', formatted)

    def _english_airport_name(self, icao, weather_data):
        if self.airport_frequency_service:
            from_csv = self.airport_frequency_service.get_airport_name(icao)
            if from_csv:
                return from_csv
        mapped = self.ENGLISH_AIRPORT_NAMES.get((icao or '').upper())
        if mapped:
            return mapped
        if weather_data and weather_data.get('name'):
            return weather_data['name'].split(',')[0].replace('/', ' ').strip()
        return icao

    def _chinese_airport_name(self, icao):
        return self.CHINA_AIRPORT_NAMES.get((icao or '').upper(), icao)

    def _digits_enunciated_zh(self, text, use_liang=False):
        digit_map = {'0': '洞', '1': '幺', '2': '两' if use_liang else '二', '3': '三', '4': '四', '5': '五', '6': '六', '7': '拐', '8': '八', '9': '九'}
        return ''.join(digit_map.get(ch, ch) for ch in str(text))

    def _digits_display(self, text):
        return str(text)

    def _format_visibility(self, weather_data):
        visib = (weather_data or {}).get('visib', '')
        if isinstance(visib, (int, float)):
            meters = int(float(visib) * 1000)
            return f"{meters} meters", f"{self._digits_display(meters)}米"
        visib_text = str(visib).strip()
        if visib_text.endswith('+') and visib_text[:-1].isdigit():
            meters = int(visib_text[:-1]) * 1000
            return f"{meters} meters", f"{self._digits_display(meters)}米"
        if visib_text.isdigit():
            return f"{visib_text} meters", f"{self._digits_display(visib_text)}米"
        return visib_text or "Unknown", visib_text or "不详"

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
        runway_text = ' / '.join(runways)
        primary = runways[0]
        if len(runways) == 1:
            return f"ILS runway {primary} approach in use.", f"使用跑道{primary}。"
        return f"ILS runway {primary} approach in use. Parallel runway {runway_text}.", f"使用跑道{runway_text}。"

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
            self.cached_atis[icao] = {'hash': '', 'text': '', 'letter_idx': 0}
        
        info_letter = self.PHONETIC[self.cached_atis[icao]['letter_idx'] % 26]
        
        report_time_en = "Unknown"
        report_time_zh = "不详"
        
        if weather_data:
            report_time_en, report_time_zh = self._format_report_time(weather_data, metar_raw)
        airport_name_en = self._english_airport_name(icao, weather_data)
        airport_name_zh = self._chinese_airport_name(icao)
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

        chinese_atis = f"""
{airport_name_zh}自动终端情报通播 {info_letter}。
{self._digits_display(report_time_en.split()[0])} Zulu。
{wind_zh}
能见度{visibility_zh}。
{clouds_zh}。
{temp_zh}
{qnh_zh}
{runway_zh}
首次联系管制时，请通报已收到通播{info_letter}。
""".strip()

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
        
        # Increment letter
        if icao in self.cached_atis:
            self.cached_atis[icao]['letter_idx'] = (self.cached_atis[icao]['letter_idx'] + 1) % 26
        
        atis_text = self._parse_metar_to_atis(icao, metar_raw, weather_data)
        
        self.cached_atis[icao] = {
            'hash': metar_hash,
            'text': atis_text,
            'letter_idx': self.cached_atis.get(icao, {}).get('letter_idx', 0)
        }
        
        print(f"ATISGenerator: New ATIS for {icao}: Information {self.PHONETIC[self.cached_atis[icao]['letter_idx']]}")
        if icao in self.pending_playback:
            self.pending_playback.discard(icao)
            self._emit_atis_log(icao, atis_text)
            event_bus.emit('atis_tts_request', atis_text, icao)
    
    def on_atis_request(self, icao):
        """Play ATIS for the given airport."""
        if not icao or icao == 'N/A':
            print("ATISGenerator: No valid ICAO for ATIS request.")
            return
        
        if icao in self.cached_atis and self.cached_atis[icao]['text']:
            atis_text = self.cached_atis[icao]['text']
            print(f"ATISGenerator: Playing cached ATIS for {icao}")
            self._emit_atis_log(icao, atis_text)
            # Emit TTS request for ATIS (use a neutral voice)
            event_bus.emit('atis_tts_request', atis_text, icao)
        else:
            # No cached ATIS - request METAR first
            print(f"ATISGenerator: No cached ATIS for {icao}. Fetching METAR...")
            self.pending_playback.add(icao)
            event_bus.emit('metar_fetch_request', icao)
