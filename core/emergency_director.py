"""
Emergency Director - Injects random failures and emergency scenarios for training.
"""
import random
import time
import threading
import os
from .context import event_bus


class EmergencyDirector:
    """Director system for injecting random emergencies and failures."""
    
    # Default probability table (per minute)
    DEFAULT_PROBABILITIES = {
        'engine_fire': 0.01,      # 1% chance per minute
        'engine_failure': 0.02,   # 2% chance per minute
        'gear_stuck': 0.03,       # 3% chance per minute
        'hydraulic_fail': 0.02,   # 2% chance per minute
        'electrical_fail': 0.01,  # 1% chance per minute
        'bird_strike': 0.02       # 2% chance per minute
    }
    
    # Emergency prompts for LLM
    EMERGENCY_PROMPTS = {
        'engine_fire': """
            SYSTEM ALERT: Aircraft Engine 1 Fire detected. Pilot has declared MAYDAY.
            ATC Action: Clear airspace immediately. Offer vectors to nearest airport.
            DO NOT ask for squawk code - use emergency squawk 7700 assumed.
            Priority: IMMEDIATE. Guide pilot to nearest suitable runway.
        """,
        'engine_failure': """
            SYSTEM ALERT: Aircraft Engine 1 Failure. Pilot has declared PAN PAN.
            ATC Action: Acknowledge emergency. Offer priority vectors.
            Suggest nearest airports with adequate runway length.
        """,
        'gear_stuck': """
            SYSTEM ALERT: Landing gear malfunction reported. Gear only partially extended.
            ATC Action: Suggest low pass for visual inspection by tower.
            Prepare emergency services if landing with gear issues.
        """,
        'hydraulic_fail': """
            SYSTEM ALERT: Hydraulic system failure. Flight controls degraded.
            ATC Action: Clear traffic. Provide extended final approach.
            Pilot may need additional time for manual procedures.
        """,
        'electrical_fail': """
            SYSTEM ALERT: Electrical system failure. Limited avionics available.
            ATC Action: Provide verbal navigation assistance.
            Pilot may have limited radio capability - speak slowly and clearly.
        """,
        'bird_strike': """
            SYSTEM ALERT: Bird strike on Engine {engine_num}! Possible damage detected.
            ATC Action: Offer immediate return to departure airport.
            Request pilot status and intentions.
            NOTE: Bird strikes can only occur in flight (altitude > 100ft AGL).
        """
    }
    
    def __init__(self, config, socketio):
        self.config = config
        self.socketio = socketio
        self.enabled = config.get('emergency', {}).get('enabled', False)
        
        # Emergency Probability Level: 'none', 'low', 'medium', 'high'
        self.probability_level = config.get('emergency', {}).get('level', 'low')
        
        # Load custom probabilities or use defaults
        self.base_probabilities = config.get('emergency', {}).get(
            'probabilities', 
            self.DEFAULT_PROBABILITIES.copy()
        )
        
        self.check_interval = config.get('emergency', {}).get('check_interval', 60)  # seconds
        
        self.running = False
        self.thread = None
        self.active_emergency = None
        
        # Sound files for warnings
        self.sound_dir = "static/sounds"
        
        # Subscribe to events
        event_bus.on('config_updated', self._on_config_update)
        
        if self.enabled:
            print(f"EmergencyDirector: Enabled. Level: {self.probability_level}")
        else:
            print("EmergencyDirector: Disabled (set emergency.enabled=true to activate)")
    
    def start(self):
        """Start the emergency monitoring thread."""
        if not self.enabled:
            return

        if self.running:
            return
            
        self.running = True
        self.thread = threading.Thread(target=self._check_loop, daemon=True)
        self.thread.start()
        print(f"EmergencyDirector: Thread started (Level: {self.probability_level})")

    def stop(self):
        """Stop the monitoring thread."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
            self.thread = None
        print("EmergencyDirector: Thread stopped")

    def _get_probability_multiplier(self):
        """Get multiplier based on probability level."""
        levels = {
            'none': 0.0,
            'low': 0.25,
            'medium': 1.0,
            'high': 5.0
        }
        return levels.get(self.probability_level, 0.25)

    def _on_config_update(self, new_config):
        """Handle config changes."""
        new_enabled = new_config.get('emergency', {}).get('enabled', False)
        self.probability_level = new_config.get('emergency', {}).get('level', 'low')
        
        self.base_probabilities = new_config.get('emergency', {}).get(
            'probabilities',
            self.DEFAULT_PROBABILITIES.copy()
        )
        
        if new_enabled and not self.running:
            self.enabled = True
            self.start()
        elif not new_enabled and self.running:
            self.stop()
            self.enabled = False

    def _check_loop(self):
        """Main loop - checks for random emergency each interval."""
        while self.running:
            # Wait for interval
            time.sleep(self.check_interval)
            
            if not self.running:
                break
            
            # Don't trigger new emergency if one is active
            if self.active_emergency:
                continue
            
            # Apply multiplier
            multiplier = self._get_probability_multiplier()
            if multiplier <= 0:
                continue

            # Roll dice for each emergency type
            for event_type, base_prob in self.base_probabilities.items():
                probability = base_prob * multiplier
                
                # Bird strike only happens in flight (not on ground)
                if event_type == 'bird_strike':
                    if not self._is_airborne():
                        continue  # Skip if on ground
                
                if random.random() < probability:
                    self._trigger_emergency(event_type)
                    break  # Only one emergency at a time

    def _trigger_emergency(self, event_type):
        """Trigger an emergency event."""
        print(f"EmergencyDirector: 🚨 EMERGENCY TRIGGERED: {event_type}")
        
        self.active_emergency = event_type
        
        # Determine specific system/engine
        system_detail = None
        engine_num = 1
        
        if event_type in ['engine_fire', 'engine_failure', 'bird_strike']:
            engine_num = self._get_random_engine_num()
        elif event_type == 'hydraulic_fail':
            system_detail = random.choice(['System A', 'System B', 'Standby System'])
        elif event_type == 'electrical_fail':
            system_detail = random.choice(['AC Bus 1', 'AC Bus 2', 'DC Bat Bus', 'Standby Power'])
        
        # Play warning sound
        self._play_warning_sound(event_type)
        
        # Inject SimConnect event (if applicable)
        self._inject_simconnect_event(event_type, engine_num, system_detail)
        
        # Inject high-priority LLM prompt
        prompt = self.EMERGENCY_PROMPTS.get(event_type, '')
        if prompt:
            # Bird strike prompt needs engine_num
            if event_type == 'bird_strike':
                prompt = prompt.format(engine_num=engine_num)
            event_bus.emit('emergency_llm_inject', {
                'type': event_type,
                'prompt': prompt.strip()
            })
        
        # Notify UI
        message = self._get_alert_message(event_type, engine_num)
        self.socketio.emit('emergency_alert', {
            'type': event_type,
            'message': message
        })
        
        # Clear active emergency after 5 minutes (allow new one)
        def clear_emergency():
            time.sleep(300)  # 5 minutes
            if self.active_emergency == event_type:
                self.active_emergency = None
                print(f"EmergencyDirector: Emergency {event_type} cleared")
        
        threading.Thread(target=clear_emergency, daemon=True).start()
    
    def _inject_simconnect_event(self, event_type, engine_num=1, system_detail=None):
        """Inject SimConnect failure event."""
        # Map engine_num to SimConnect event suffix
        engine_suffix = f'ENGINE{engine_num}' if engine_num else 'ENGINE1'
        simconnect_events = {
            'engine_fire': f'TOGGLE_{engine_suffix}_FAILURE',
            'engine_failure': f'TOGGLE_{engine_suffix}_FAILURE',
            'gear_stuck': None,  # Handled differently
            'hydraulic_fail': 'TOGGLE_HYDRAULIC_FAILURE',
            'electrical_fail': 'TOGGLE_ELECTRICAL_FAILURE',
            'bird_strike': f'TOGGLE_{engine_suffix}_FAILURE'
        }
        
        event_name = simconnect_events.get(event_type)
        if event_name:
            # TODO: SimConnect usually requires specific indices for systems, 
            # but simple TOGGLE_* events might be generic. 
            # For deeper system failure, we need more specific SimConnect events or variables.
            # Currently just toggling main failure as placeholder for specific system.
            event_bus.emit('simconnect_event', {'event': event_name})
        
        event_name = simconnect_events.get(event_type)
        if event_name:
            event_bus.emit('simconnect_event', {'event': event_name})
    
    def _play_warning_sound(self, event_type):
        """Emit warning sound to frontend."""
        sound_map = {
            'engine_fire': 'fire_warning.mp3',
            'engine_failure': 'master_caution.mp3',
            'gear_stuck': 'gear_warning.mp3',
            'hydraulic_fail': 'master_caution.mp3',
            'electrical_fail': 'master_caution.mp3',
            'bird_strike': 'master_caution.mp3'
        }
        
        sound_file = sound_map.get(event_type, 'master_caution.mp3')
        self.socketio.emit('play_warning_sound', {'sound': sound_file})
    
    def _get_alert_message(self, event_type, engine_num=1, system_detail=None):
        """Get user-friendly alert message."""
        sys_str = f" ({system_detail})" if system_detail else ""
        messages = {
            'engine_fire': f'🔥 ENGINE {engine_num} FIRE! Declare emergency!',
            'engine_failure': f'⚠️ ENGINE {engine_num} FAILURE! Check engine parameters!',
            'gear_stuck': '⚙️ GEAR MALFUNCTION! Landing gear not responding!',
            'hydraulic_fail': f'🛢️ HYDRAULIC FAILURE{sys_str}! Flight controls degraded!',
            'electrical_fail': f'⚡ ELECTRICAL FAILURE{sys_str}! Systems offline!',
            'bird_strike': f'🐦 BIRD STRIKE on Engine {engine_num}! Inspect engines!'
        }
        return messages.get(event_type, f'⚠️ Emergency: {event_type}')
    
    def trigger_manual(self, event_type):
        """Manually trigger an emergency (for testing)."""
        if event_type in self.probabilities:
            self._trigger_emergency(event_type)
            return True
        return False
    
    def clear_emergency(self):
        """Clear current active emergency."""
        if self.active_emergency:
            event_type = self.active_emergency
            self.active_emergency = None
            print(f"EmergencyDirector: Emergency {event_type} manually cleared")
            self.socketio.emit('emergency_cleared', {'type': event_type})
            return True
        return False
    
    def _is_airborne(self):
        """Check if aircraft is airborne (not on ground and altitude > 100ft)."""
        from .context import shared_context, context_lock
        
        with context_lock:
            on_ground = shared_context['aircraft'].get('on_ground', True)
            altitude = shared_context['aircraft'].get('altitude', 0)
        
        # Airborne = not on ground AND altitude > 100ft AGL
        return not on_ground and altitude > 100
    
    def _get_random_engine_num(self):
        """Get random engine number for multi-engine aircraft alerts."""
        # Most airliners have 2-4 engines
        return random.choice([1, 2])
