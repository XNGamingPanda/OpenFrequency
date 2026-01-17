
class EventTrigger:
    """
    Monitors simulation data for specific events and triggers actions.
    (e.g., ATIS updates, altitude deviations, handoffs).
    """
    def __init__(self, config, socketio):
        """
        Initializes the EventTrigger.

        Args:
            config (dict): The application configuration.
            socketio: The Flask-SocketIO instance.
        """
        self.config = config
        self.socketio = socketio
        self.last_qnh = None
        print("EventTrigger initialized.")

    def check(self, sim_data):
        """
        Checks the current simulation data for any triggerable events.

        Args:
            sim_data (dict): The latest data from the simulator.
        """
        # This is a placeholder. Real implementation will go here.
        # For example, check for QNH changes for ATIS updates.
        if sim_data and 'qnh' in sim_data:
            current_qnh = sim_data['qnh']
            if self.last_qnh is None:
                self.last_qnh = current_qnh
            elif abs(current_qnh - self.last_qnh) > 0.02:
                print(f"EVENT: QNH changed from {self.last_qnh:.2f} to {current_qnh:.2f}. Triggering ATIS update.")
                # self.socketio.emit('atis_update', {'qnh': current_qnh})
                self.last_qnh = current_qnh
