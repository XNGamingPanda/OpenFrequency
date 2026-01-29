"""
Chatter Generator - Generates background ATC/pilot chatter based on traffic events.
Uses template slot-filling for fast generation without LLM.
"""
import json
import random
import os
from typing import Dict, Any, Optional
from .context import event_bus, shared_context, context_lock

class ChatterGenerator:
    """
    Listens to traffic_state_change events and generates appropriate
    ATC/pilot dialogue using template slot-filling.
    """
    
    # Ghost callsign prefixes for unnamed aircraft
    GHOST_CALLSIGNS = [
        "November", "Alpha", "Bravo", "Charlie", "Delta",
        "November", "Kilo", "Mike", "Papa", "Romeo", "Sierra"
    ]
    
    # Frequency types and their values
    FREQ_VALUES = {
        'ground': '121.9',
        'tower': '118.1',
        'departure': '119.2',
        'approach': '119.5',
        'center': '124.5'
    }
    
    def __init__(self, config, tts_engine):
        self.config = config
        self.tts_engine = tts_engine
        self.templates = self._load_templates()
        self.enabled = config.get('traffic', {}).get('chatter_enabled', True)
        
        # Subscribe to traffic events
        event_bus.on('traffic_state_change', self._on_traffic_event)
        
        print(f"ChatterGenerator: Initialized with {len(self.templates)} template categories.")
    
    def _load_templates(self) -> Dict[str, list]:
        """Load chatter templates from JSON file."""
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'data', 'chatter_templates.json'
        )
        
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"ChatterGenerator: Failed to load templates: {e}")
            return {}
    
    def _on_traffic_event(self, event_data: Dict[str, Any]):
        """Handle traffic state change events."""
        if not self.enabled:
            return
        
        callsign = event_data.get('callsign', '')
        new_state = event_data.get('new_state', '')
        old_state = event_data.get('old_state', '')
        voice_id = event_data.get('voice_id')
        
        # Generate appropriate chatter based on state transition
        chatter = self._generate_chatter(callsign, old_state, new_state, event_data)
        
        if chatter:
            # Queue for TTS with background priority
            self._queue_chatter(chatter, voice_id, is_atc=chatter.get('is_atc', True))
    
    def _generate_chatter(self, callsign: str, old_state: str, new_state: str, 
                          data: Dict[str, Any]) -> Optional[Dict]:
        """Generate chatter text based on state transition."""
        
        # Handle ghost callsigns
        if not callsign or callsign == 'N/A':
            callsign = self._generate_ghost_callsign()
        
        # Build context for slot filling
        context = self._build_context(callsign, data)
        
        # Determine which templates to use based on state transition
        template_key, is_atc = self._get_template_key(old_state, new_state)
        
        if not template_key or template_key not in self.templates:
            return None
        
        # Select random template
        templates = self.templates[template_key]
        template = random.choice(templates)
        
        # Fill slots
        text = self._fill_slots(template, context)
        
        return {
            'text': text,
            'is_atc': is_atc,
            'callsign': callsign,
            'state': new_state
        }
    
    def _get_template_key(self, old_state: str, new_state: str) -> tuple:
        """
        Get template key and speaker type based on state transition.
        Returns (template_key, is_atc)
        """
        transitions = {
            # Pushback
            ('PARKED', 'PUSHBACK'): ('pushback_clearance', True),
            
            # Taxi
            ('PUSHBACK', 'TAXIING'): ('taxi_clearance', True),
            ('PARKED', 'TAXIING'): ('taxi_clearance', True),
            
            # Takeoff
            ('TAXIING', 'TAKEOFF_ROLL'): ('takeoff_clearance', True),
            
            # Airborne
            ('TAKEOFF_ROLL', 'AIRBORNE'): ('departure_handoff', True),
            
            # Approach
            ('AIRBORNE', 'APPROACH'): ('approach_clearance', True),
            
            # Landing  
            ('APPROACH', 'LANDING'): ('landing_clearance', True),
            
            # Vacate
            ('LANDING', 'VACATING'): ('vacate_instruction', True),
            ('LANDING', 'TAXIING'): ('vacate_instruction', True),
        }
        
        key = (old_state, new_state)
        return transitions.get(key, (None, True))
    
    def _build_context(self, callsign: str, data: Dict[str, Any]) -> Dict[str, str]:
        """Build slot-filling context."""
        # Get environment data
        with context_lock:
            nearest_apt = shared_context['environment'].get('nearest_airport', 'KJFK')
            com1 = shared_context['aircraft'].get('com1_freq', 118.1)
        
        # Generate plausible values
        runways = ['01', '09', '18', '27', '36L', '36R', '09L', '09R']
        taxiways = ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo', 'Foxtrot']
        winds = ['calm', '270 at 5', '180 at 10', '090 at 8', '360 at 12']
        
        return {
            'callsign': self._format_callsign(callsign),
            'rwy': random.choice(runways),
            'taxiway': random.choice(taxiways),
            'wind': random.choice(winds),
            'freq': f"{random.choice([118.1, 119.2, 121.9, 124.5]):.1f}",
            'hdg': str(random.randint(1, 36) * 10).zfill(3),
            'alt': str(random.choice([3000, 4000, 5000, 6000, 10000, 15000])),
            'oclock': str(random.randint(1, 12)),
            'distance': str(random.randint(2, 10)),
            'alt_diff': random.choice(['same altitude', '1000 above', '500 below'])
        }
    
    def _format_callsign(self, callsign: str) -> str:
        """Format callsign for radio presentation."""
        # Convert airline codes to spoken format
        airline_map = {
            'CPA': 'Cathay',
            'CSN': 'China Southern',
            'CCA': 'Air China',
            'CES': 'China Eastern',
            'UAL': 'United',
            'AAL': 'American',
            'DAL': 'Delta',
            'SWA': 'Southwest',
            'BAW': 'Speedbird',
            'DLH': 'Lufthansa',
            'AFR': 'Air France',
            'JAL': 'Japan Air',
            'ANA': 'All Nippon',
        }
        
        # Check if starts with airline code
        for code, name in airline_map.items():
            if callsign.upper().startswith(code):
                flight_num = callsign[3:]
                return f"{name} {flight_num}"
        
        return callsign
    
    def _fill_slots(self, template: str, context: Dict[str, str]) -> str:
        """Fill template slots with context values."""
        result = template
        for key, value in context.items():
            result = result.replace(f'{{{key}}}', value)
        return result
    
    def _generate_ghost_callsign(self) -> str:
        """Generate a callsign for unnamed aircraft."""
        prefix = random.choice(self.GHOST_CALLSIGNS)
        numbers = ''.join([str(random.randint(0, 9)) for _ in range(3)])
        return f"{prefix} {numbers}"
    
    def _queue_chatter(self, chatter: Dict, voice_id: str, is_atc: bool):
        """Queue chatter for TTS playback."""
        text = chatter.get('text', '')
        if not text:
            return
        
        # For now, emit directly to TTS
        # Future: implement priority queue with ducking
        print(f"ChatterGenerator: [{chatter.get('callsign')}] {text}")
        
        # Emit to a special chatter TTS event (background priority)
        event_bus.emit('chatter_tts_request', {
            'text': text,
            'voice': voice_id if not is_atc else None,  # ATC uses default region voice
            'priority': 3,  # Background priority
            'is_atc': is_atc
        })
    
    def set_enabled(self, enabled: bool):
        """Enable or disable chatter generation."""
        self.enabled = enabled
        print(f"ChatterGenerator: {'Enabled' if enabled else 'Disabled'}")
