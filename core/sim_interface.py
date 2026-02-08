"""
Simulator Interface - Abstraction layer for MSFS and P3D compatibility.
Detects running simulator and loads appropriate SimConnect library.
"""
import os
import time


class SimInterface:
    """Abstract interface for simulator connectivity."""
    
    # Simulator process names
    SIM_PROCESSES = {
        'msfs': ['FlightSimulator.exe', 'Microsoft.FlightSimulator.exe'],
        'p3d': ['Prepar3D.exe']
    }
    
    def __init__(self, config):
        self.config = config
        self.sim_type = None
        self.sm = None
        self.aq = None
        
        # Force P3D mode from config (debug option)
        self.force_p3d = config.get('debug', {}).get('force_p3d', False)
        
        print("SimInterface: Initialized (Auto-detect mode)")
    
    def detect_simulator(self):
        """Detect which simulator is running."""
        if self.force_p3d:
            print("SimInterface: Forcing P3D mode (debug)")
            return 'p3d'
        
        try:
            import psutil
            process_names = [p.name() for p in psutil.process_iter(['name'])]
            
            for name in self.SIM_PROCESSES['p3d']:
                if name in process_names:
                    print(f"SimInterface: Detected Prepar3D ({name})")
                    return 'p3d'
            
            for name in self.SIM_PROCESSES['msfs']:
                if name in process_names:
                    print(f"SimInterface: Detected MSFS ({name})")
                    return 'msfs'
            
            print("SimInterface: No simulator detected, defaulting to MSFS")
            return 'msfs'
            
        except ImportError:
            print("SimInterface: psutil not installed, defaulting to MSFS")
            return 'msfs'
    
    def connect(self):
        """Connect to the detected simulator."""
        self.sim_type = self.detect_simulator()
        
        try:
            if self.sim_type == 'p3d':
                return self._connect_p3d()
            else:
                return self._connect_msfs()
        except Exception as e:
            print(f"SimInterface: Connection failed: {e}")
            return False
    
    def _connect_msfs(self):
        """Connect to MSFS using standard SimConnect."""
        from SimConnect import SimConnect, AircraftRequests
        
        self.sm = SimConnect()
        self.aq = AircraftRequests(self.sm, _time=2000)
        print("SimInterface: Connected to MSFS")
        return True
    
    def _connect_p3d(self):
        """Connect to P3D - uses same SimConnect but with different DLL path."""
        # P3D uses the same SimConnect Python library
        # The DLL is typically in the P3D installation or SDK
        from SimConnect import SimConnect, AircraftRequests
        
        # P3D SimConnect DLL path (common locations)
        p3d_dll_paths = [
            r"C:\Program Files\Lockheed Martin\Prepar3D v5\SimConnect.dll",
            r"C:\Program Files\Lockheed Martin\Prepar3D v4\SimConnect.dll",
            r"C:\Program Files (x86)\Lockheed Martin\Prepar3D v5\SimConnect.dll",
            "./lib/P3D_SimConnect.dll"
        ]
        
        # Find first existing DLL
        dll_path = None
        for path in p3d_dll_paths:
            if os.path.exists(path):
                dll_path = path
                break
        
        if dll_path:
            print(f"SimInterface: Using P3D SimConnect DLL: {dll_path}")
            # Note: Python SimConnect library may need modification to accept custom DLL
            # For now, we rely on the standard path resolution
        
        self.sm = SimConnect()
        self.aq = AircraftRequests(self.sm, _time=2000)
        print("SimInterface: Connected to P3D")
        return True
    
    def get(self, var_name):
        """Get a SimConnect variable with P3D compatibility mapping."""
        if not self.aq:
            return None
        
        # Variable mapping for P3D differences
        p3d_var_map = {
            # Most variables are the same, but some weather vars differ
            # Add mappings here if needed
        }
        
        actual_var = p3d_var_map.get(var_name, var_name)
        return self.aq.get(actual_var)
    
    def disconnect(self):
        """Disconnect from simulator."""
        if self.sm:
            try:
                self.sm.quit()
            except:
                pass
        self.sm = None
        self.aq = None
