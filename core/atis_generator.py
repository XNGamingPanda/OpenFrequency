"""
ATIS Generator - Generates ATIS broadcast from METAR data.
Issue 7: Create ATIS broadcast, cache until METAR changes.
"""
import hashlib
from .context import event_bus


class ATISGenerator:
    """Generates and caches ATIS broadcasts from METAR data."""
    
    # Phonetic alphabet for ATIS information letter
    PHONETIC = ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo', 'Foxtrot', 
                'Golf', 'Hotel', 'India', 'Juliet', 'Kilo', 'Lima', 'Mike',
                'November', 'Oscar', 'Papa', 'Quebec', 'Romeo', 'Sierra', 
                'Tango', 'Uniform', 'Victor', 'Whiskey', 'X-ray', 'Yankee', 'Zulu']
    
    def __init__(self, config, socketio):
        self.config = config
        self.socketio = socketio
        self.cached_atis = {}  # {icao: {'hash': ..., 'text': ..., 'letter_idx': ...}}
        
        event_bus.on('atis_playback_request', self.on_atis_request)
        event_bus.on('metar_updated', self.on_metar_updated)
        print("ATISGenerator: Initialized.")
    
    def _parse_metar_to_atis(self, icao, metar_raw, weather_data=None):
        """Convert METAR to spoken ATIS format."""
        # Get or increment information letter
        if icao not in self.cached_atis:
            self.cached_atis[icao] = {'hash': '', 'text': '', 'letter_idx': 0}
        
        info_letter = self.PHONETIC[self.cached_atis[icao]['letter_idx'] % 26]
        
        # Parse components from weather_data if available
        wind = "Calm"
        visibility = "10 kilometers"
        clouds = "Clear"
        temp = "Unknown"
        dew = "Unknown"
        altimeter = "Unknown"
        
        if weather_data:
            # Wind
            wdir = weather_data.get('wdir', 0)
            wspd = weather_data.get('wspd', 0)
            wgst = weather_data.get('wgst', 0)
            if wspd > 0:
                wind = f"{wdir:03d} degrees at {wspd} knots"
                if wgst and wgst > wspd + 5:
                    wind += f", gusting {wgst}"
            
            # Visibility
            visib = weather_data.get('visib', 'CAVOK')
            if isinstance(visib, (int, float)):
                visibility = f"{visib} statute miles" if visib < 10 else "Greater than 10 miles"
            else:
                visibility = str(visib)
            
            # Clouds
            clouds_data = weather_data.get('clouds', [])
            if clouds_data:
                cloud_str_parts = []
                for c in clouds_data[:3]:  # Max 3 layers
                    cover = c.get('cover', '')
                    base = c.get('base', 0)
                    if cover and base:
                        cloud_str_parts.append(f"{cover} at {base} feet")
                clouds = ", ".join(cloud_str_parts) if cloud_str_parts else "Clear"
            
            # Temperature
            temp_c = weather_data.get('temp', None)
            dewp_c = weather_data.get('dewp', None)
            if temp_c is not None:
                temp = f"{int(temp_c)} degrees Celsius"
            if dewp_c is not None:
                dew = f"{int(dewp_c)}"
            
            # Altimeter
            altim = weather_data.get('altim', None)
            if altim:
                # Convert to proper format
                if altim > 900:  # hPa
                    altimeter = f"{int(altim)} hectopascals"
                else:  # inHg
                    altimeter = f"{altim:.2f} inches"
        
        # Build ATIS text
        atis_text = f"""
{icao} Airport Information {info_letter}.
Time: Automated observation.
Wind: {wind}.
Visibility: {visibility}.
Sky condition: {clouds}.
Temperature: {temp}, Dewpoint: {dew}.
Altimeter: {altimeter}.
Advise on initial contact you have information {info_letter}.
"""
        return atis_text.strip()
    
    def on_metar_updated(self, icao, metar_raw, weather_data):
        """Called when METAR is updated. Regenerate ATIS if changed."""
        metar_hash = hashlib.md5(metar_raw.encode()).hexdigest()
        
        if icao in self.cached_atis and self.cached_atis[icao]['hash'] == metar_hash:
            # METAR unchanged, use cached ATIS
            print(f"ATISGenerator: METAR unchanged for {icao}, using cached ATIS.")
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
    
    def on_atis_request(self, icao):
        """Play ATIS for the given airport."""
        if not icao or icao == 'N/A':
            print("ATISGenerator: No valid ICAO for ATIS request.")
            return
        
        if icao in self.cached_atis and self.cached_atis[icao]['text']:
            atis_text = self.cached_atis[icao]['text']
            print(f"ATISGenerator: Playing cached ATIS for {icao}")
            # Emit TTS request for ATIS (use a neutral voice)
            event_bus.emit('atis_tts_request', atis_text, icao)
        else:
            # No cached ATIS - request METAR first
            print(f"ATISGenerator: No cached ATIS for {icao}. Fetching METAR...")
            event_bus.emit('metar_fetch_request', icao)
