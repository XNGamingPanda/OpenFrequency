import threading

# 1. Shared State (Context) with Lock
# This dictionary holds the global state shared across all threads.
# Access should be controlled via the provided lock.
shared_context = {
    "aircraft": {
        "callsign": "N/A",
        "latitude": 0.0, "longitude": 0.0, "altitude": 0, "airspeed": 0, "heading": 0,
        "on_ground": True,
        "gear_handle": "DOWN",
        "com1_freq": 0.0,
        "transponder": "0000"
    },
    "environment": {
        "qnh": 29.92,
        "zulu_time": "00:00",
        "nearest_airport": "N/A"
    },
    "atc_state": {
        "current_controller": "N/A",
        "last_instruction": "",
        "expecting_readback": False,
        "is_busy": False
    },
    "navigation": {
        "current_taxi_path": []
    },
    "flight_plan": {
        "origin": "N/A",
        "destination": "N/A",
        "alternate": "N/A",
        "route": "N/A",
        "cruise_alt": 0,
        "flight_number": "N/A"
    }
}

context_lock = threading.Lock()

# 2. Simple Event Bus (Pub/Sub)
# Used for decoupled communication between modules/threads.
class EventBus:
    def __init__(self):
        self.listeners = {}

    def on(self, event_name, callback):
        if event_name not in self.listeners:
            self.listeners[event_name] = []
        self.listeners[event_name].append(callback)

    def emit(self, event_name, *args, **kwargs):
        if event_name in self.listeners:
            for callback in self.listeners[event_name]:
                try:
                    callback(*args, **kwargs)
                except Exception as e:
                    print(f"Error in event bus callback for '{event_name}': {e}")

# Global instance
event_bus = EventBus()
