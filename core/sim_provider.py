"""
SimProvider - Abstract base class for simulator adapters.
Enables support for MSFS, P3D, and X-Plane through a unified interface.
"""
from abc import ABC, abstractmethod


class SimProvider(ABC):
    """Abstract base class for flight simulator data providers."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of the simulator."""
        pass
    
    @abstractmethod
    def connect(self) -> bool:
        """Attempt to connect to the simulator. Returns True on success."""
        pass
    
    @abstractmethod
    def disconnect(self):
        """Disconnect from the simulator."""
        pass
    
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if currently connected to the simulator."""
        pass
    
    # ===== READ OPERATIONS =====
    
    @abstractmethod
    def get_position(self) -> dict:
        """
        Get current aircraft position.
        Returns: {'latitude': float, 'longitude': float, 'altitude': float}
        Altitude is in feet MSL.
        """
        pass
    
    @abstractmethod
    def get_attitude(self) -> dict:
        """
        Get current aircraft attitude.
        Returns: {'heading': float, 'pitch': float, 'bank': float}
        All values in degrees.
        """
        pass
    
    @abstractmethod
    def get_airspeed(self) -> float:
        """Get indicated airspeed in knots."""
        pass
    
    @abstractmethod
    def get_vertical_speed(self) -> float:
        """Get vertical speed in feet per minute."""
        pass
    
    @abstractmethod
    def get_engine_data(self) -> dict:
        """
        Get engine parameters.
        Returns: {'n1': float, 'egt': float, 'fuel_flow': float}
        """
        pass
    
    @abstractmethod
    def get_gear_status(self) -> bool:
        """Returns True if gear is down."""
        pass
    
    @abstractmethod
    def get_flaps_position(self) -> float:
        """Get flaps position as percentage (0-100)."""
        pass
    
    # ===== WRITE OPERATIONS =====
    
    @abstractmethod
    def set_transponder(self, code: int):
        """Set transponder code (e.g., 7700)."""
        pass
    
    @abstractmethod
    def set_com1_frequency(self, frequency: float):
        """Set COM1 active frequency (e.g., 118.100)."""
        pass
    
    # ===== EVENTS =====
    
    @abstractmethod
    def trigger_event(self, event_name: str):
        """Trigger a simulator event (e.g., 'TOGGLE_ENGINE1_FAILURE')."""
        pass
