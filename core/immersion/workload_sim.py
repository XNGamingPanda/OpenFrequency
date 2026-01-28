import random

class WorkloadSimulator:
    def __init__(self, config):
        self.enabled = config.get('immersion', {}).get('enable_standby_simulation', True)
        self.busy_level = config.get('immersion', {}).get('busy_level', 'medium')

    def should_standby(self):
        if not self.enabled:
            return False
        
        # Simple probability simulation
        threshold = 0.3 if self.busy_level == 'high' else 0.1
        if self.busy_level == 'low': 
            threshold = 0.05
        return random.random() < threshold

    def should_ignore(self):
        """Determines if ATC completely misses the call (silence)."""
        if not self.enabled:
            return False
            
        # Ignore probabilities
        probs = {
            'low': 0.0,
            'medium': 0.05,
            'high': 0.15
        }
        threshold = probs.get(self.busy_level, 0.0)
        return random.random() < threshold