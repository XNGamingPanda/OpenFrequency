import time
import json
import random
import os
import threading
from enum import Enum, auto
from .ambience import AmbiencePlayer
from ..context import event_bus, shared_context, context_lock

class CabinState(Enum):
    UNKNOWN = auto()
    BOARDING = auto()
    CSM_PRE_DEPARTURE = auto() # Door closed
    SAFETY_DEMO = auto()
    TAKEOFF_PREP = auto()
    CLIMB_SERVICE = auto()
    CRUISE = auto()
    DESCENT = auto()
    LANDING_PREP = auto()
    TAXI_TO_GATE = auto()
    ON_BLOCKS = auto()

class Purser:
    """
    Intelligent Cabin Crew Chief.
    Manages cabin states, announcements, and interactions.
    """
    def __init__(self, config, tts_engine):
        self.config = config
        self.tts_engine = tts_engine
        self.ambience = AmbiencePlayer(config)
        self.state = CabinState.UNKNOWN
        self.last_state_change = 0
        self.scripts = self._load_scripts()
        self.airline = config.get('cabin', {}).get('airline', 'Generic')
        
        # Subscribe to telemetry
        event_bus.on('telemetry_update', self._on_telemetry)
        event_bus.on('cabin_intercom', self._on_intercom)
        event_bus.on('passenger_reaction', self._on_passenger_reaction)
        
        print(f"Purser: Initialized for airline '{self.airline}'")

    def _load_scripts(self):
        try:
            # Load from data/cabin/scripts.json
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'cabin', 'scripts.json')
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Purser: Failed to load scripts: {e}")
        return {}

    def _on_telemetry(self, data):
        """Evaluate state transitions based on flight data."""
        # 1. State Machine
        new_state = self._evaluate_state(data)
        if new_state != self.state:
            self._transition(new_state)

    def _evaluate_state(self, data):
        # Extract telemetry
        speed = data.get('airspeed', 0)
        alt = data.get('altitude', 0)
        on_ground = data.get('on_ground', True)
        n1 = data.get('n1', 0)
        parking_brake = data.get('parking_brake', False)
        
        # Logic
        if self.state == CabinState.UNKNOWN:
            if on_ground and speed < 1: return CabinState.BOARDING
            return CabinState.CRUISE # Assume mid-flight if started late
            
        if self.state == CabinState.BOARDING:
            # If engine starts (N1 > 20) or Parking Brake released -> Door Closed
            if n1 > 20 or not parking_brake:
                return CabinState.CSM_PRE_DEPARTURE
                
        if self.state == CabinState.CSM_PRE_DEPARTURE:
            # If moving (>5kts) -> Safety Demo
            if speed > 5:
                return CabinState.SAFETY_DEMO
                
        if self.state == CabinState.SAFETY_DEMO:
            # If entered runway (Heading aligned? Or just throttle up?)
            # Simplified: N1 > 70 (Takeoff thrust)
            if n1 > 70:
                return CabinState.TAKEOFF_PREP
                
        if self.state == CabinState.TAKEOFF_PREP:
            # If airborne and > 1000ft
            if not on_ground and alt > 1000:
                return CabinState.CLIMB_SERVICE # Or just CLIMB first
                
        if self.state == CabinState.CLIMB_SERVICE:
            # If climbing past 10k ft -> Service
            # If descending?
            pass # Stay here until descent
            
        # Simplified for now
        return self.state

    def _transition(self, new_state):
        print(f"Purser: Transition {self.state.name} -> {new_state.name}")
        self.state = new_state
        self.last_state_change = time.time()
        
        # Actions
        if new_state == CabinState.BOARDING:
            self.ambience.play_bgm('boarding.mp3')
            self._announce('welcome')
            
        elif new_state == CabinState.CSM_PRE_DEPARTURE:
            self.ambience.stop_bgm()
            self._announce('door_close')
            
        elif new_state == CabinState.SAFETY_DEMO:
            self._announce('safety_demo')
            
        elif new_state == CabinState.TAKEOFF_PREP:
            self._announce('takeoff_prep')
            
    def _announce(self, script_key):
        """Play announcement using TTS or pre-recorded file."""
        # 1. Get script for airline
        airline_data = self.scripts.get(self.airline, self.scripts.get('Generic', {}))
        text = airline_data.get(script_key)
        
        if not text:
            print(f"Purser: No script for {script_key}")
            return
            
        voice = airline_data.get('voice', 'en-US-JennyNeural')
        
        # 2. Queue TTS (Priority 2 - High)
        print(f"Purser: Announcing '{script_key}': {text[:30]}...")
        event_bus.emit('chatter_tts_request', {
            'text': text,
            'voice': voice,
            'is_atc': False, # It's cabin
            'priority': 2
        })

    def _on_intercom(self, action):
        """Handle user interaction from Intercom Panel."""
        print(f"Purser: Intercom request: {action}")
        if action == 'call_purser':
            self._announce('attendant_call')
        elif action == 'prepare_cabin':
            self._announce('arrival_prep')
        elif action == 'emergency':
            self._announce('brace')

    def _on_passenger_reaction(self, data):
        """Handle passenger reaction events from BlackBox."""
        reaction_type = data.get('type')
        print(f"Purser: Passenger Reaction -> {reaction_type}")
        
        if reaction_type == 'applause':
            self.ambience.play_sfx('applause.wav')
        elif reaction_type == 'scream':
            self.ambience.play_sfx('scream.wav')
        elif reaction_type == 'normal':
            # Maybe some Chatter?
            pass
