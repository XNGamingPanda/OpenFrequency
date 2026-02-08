"""
SimProviderFactory - Factory for creating the appropriate simulator provider.
Automatically detects simulator or uses config override.
"""
import psutil
from .sim_provider import SimProvider


class SimProviderFactory:
    """Factory to create the correct SimProvider based on config or auto-detection."""
    
    # Process names to detect
    SIMULATOR_PROCESSES = {
        'msfs': ['FlightSimulator.exe', 'Microsoft.FlightSimulator.exe'],
        'p3d': ['Prepar3D.exe', 'Prepar3D_v4.exe', 'Prepar3D_v5.exe', 'Prepar3D_v6.exe'],
        'xplane': ['X-Plane.exe', 'X-Plane-x86_64.exe', 'X-Plane-arm64.exe'],
        'fsx': ['fsx.exe', 'fsx-se.exe'],
    }
    
    @staticmethod
    def detect_simulator() -> str:
        """Auto-detect which simulator is running. Returns simulator key or None."""
        try:
            running_procs = {p.name().lower() for p in psutil.process_iter(['name'])}
        except Exception:
            return None
        
        for sim_key, process_names in SimProviderFactory.SIMULATOR_PROCESSES.items():
            for proc_name in process_names:
                if proc_name.lower() in running_procs:
                    print(f"SimProviderFactory: Detected {sim_key} via process {proc_name}")
                    return sim_key
        
        return None
    
    @staticmethod
    def create(config: dict) -> SimProvider:
        """
        Create a SimProvider instance based on config or auto-detection.
        
        Config options:
            simulator.provider: 'auto' | 'msfs' | 'p3d' | 'xplane' | 'fsx'
            debug.force_xplane: True/False
            debug.force_p3d: True/False
        """
        sim_config = config.get('simulator', {})
        debug_config = config.get('debug', {})
        
        # Debug overrides first
        if debug_config.get('force_xplane', False):
            provider_type = 'xplane'
        elif debug_config.get('force_p3d', False):
            provider_type = 'p3d'
        else:
            # Use config or auto-detect
            provider_type = sim_config.get('provider', 'auto')
            
            if provider_type == 'auto':
                detected = SimProviderFactory.detect_simulator()
                provider_type = detected or 'msfs'  # Default to MSFS
        
        print(f"SimProviderFactory: Creating provider for '{provider_type}'")
        
        # Create appropriate provider
        if provider_type == 'xplane':
            from .xplane_provider import XPlaneProvider
            host = sim_config.get('xplane_host', '127.0.0.1')
            port = sim_config.get('xplane_port', 49009)
            return XPlaneProvider(host=host, port=port)
        
        elif provider_type in ['msfs', 'p3d', 'fsx']:
            # MSFS/P3D/FSX all use SimConnect
            from .simconnect_provider import SimConnectProvider
            return SimConnectProvider(sim_type=provider_type)
        
        else:
            raise ValueError(f"Unknown simulator provider: {provider_type}")
    
    @staticmethod
    def get_available_simulators() -> list:
        """List all available/installed simulator options."""
        available = []
        detected = SimProviderFactory.detect_simulator()
        
        # Always include these options
        for sim_key in ['msfs', 'p3d', 'xplane', 'fsx']:
            info = {
                'key': sim_key,
                'name': {
                    'msfs': 'Microsoft Flight Simulator',
                    'p3d': 'Prepar3D',
                    'xplane': 'X-Plane',
                    'fsx': 'FSX / FSX:SE'
                }.get(sim_key, sim_key),
                'running': sim_key == detected
            }
            available.append(info)
        
        return available
