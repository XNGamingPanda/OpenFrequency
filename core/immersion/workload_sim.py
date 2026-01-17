import random

class WorkloadSimulator:
    def __init__(self, config):
        self.enabled = config.get('immersion', {}).get('enable_standby_simulation', True)
        self.busy_level = config.get('immersion', {}).get('busy_level', 'medium')

    def should_standby(self):
        if not self.enabled:
            return False
        
        # 简单概率模拟，后期可结合机场繁忙程度
        threshold = 0.3 if self.busy_level == 'high' else 0.1
        return random.random() < threshold