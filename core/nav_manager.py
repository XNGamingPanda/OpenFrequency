import sqlite3
import threading
import time

from .ground_data_service import GroundDataService

class NavManager:
    def __init__(self, config, context, lock, bus, ground_service=None, airport_frequency_service=None):
        self.config = config
        self.context = context
        self.lock = lock
        self.bus = bus
        self.sqlite_path = config.get('navdata', {}).get('sqlite_path', '')
        self.conn = None
        self.ground_service = ground_service or GroundDataService(config, airport_frequency_service=airport_frequency_service)
        self.last_broadcast_qnh = None
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self.bus.on('config_updated', self._on_config_updated)
        print("NavManager: Initialized.")

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _on_config_updated(self, new_config):
        self.config = new_config
        self.sqlite_path = new_config.get('navdata', {}).get('sqlite_path', '')
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None
        if self.ground_service:
            self.ground_service.update_config(new_config)

    def _should_use_sqlite_fallback(self):
        nav_config = self.config.get('navdata', {}) or {}
        source = (nav_config.get('source') or '').strip().lower()
        if source not in {'littlenavmap', 'sqlite'}:
            return False
        if not self.sqlite_path or 'path/to/db' in self.sqlite_path:
            return False
        return True

    def _loop(self):
        print("NavManager: Thread started.")
        while not self._stop_event.is_set():
            try:
                if self._should_use_sqlite_fallback() and not self.conn:
                    self.conn = sqlite3.connect(self.sqlite_path, check_same_thread=False)
                
                with self.lock:
                    lat = self.context['aircraft'].get('latitude', self.context['aircraft'].get('lat', 0.0))
                    lon = self.context['aircraft'].get('longitude', self.context['aircraft'].get('lon', 0.0))
                    current_qnh = self.context['environment']['qnh']

                # 1. Find nearest airport
                nearest_icao = self._find_nearest_airport(lat, lon)
                with self.lock:
                    self.context['environment']['nearest_airport'] = nearest_icao

                # 2. Check for QNH changes (Immersion Engine task)
                if self.last_broadcast_qnh is None:
                    self.last_broadcast_qnh = current_qnh
                
                if abs(current_qnh - self.last_broadcast_qnh) > 0.02:
                    self.bus.emit('atc_broadcast', f"All stations, information is now current. QNH {current_qnh:.2f}.")
                    self.last_broadcast_qnh = current_qnh
            
            except Exception as e:
                print(f"Error in NavManager loop: {e}")
                if self.conn:
                    self.conn.close()
                self.conn = None

            time.sleep(5) # Low frequency thread

    def _find_nearest_airport(self, lat, lon):
        simulator_airport = self.ground_service.get_nearest_airport(lat, lon)
        if simulator_airport:
            return simulator_airport

        if not self._should_use_sqlite_fallback():
            return "N/A"

        if not self.conn:
            try:
                self.conn = sqlite3.connect(self.sqlite_path, check_same_thread=False)
            except Exception:
                self.conn = None
                return "N/A"
        
        cursor = self.conn.cursor()
        # Simple bounding box query
        cursor.execute("""
            SELECT ident, laty, lonx FROM airport
            WHERE laty BETWEEN ? AND ? AND lonx BETWEEN ? AND ?
            LIMIT 20
        """, (lat - 0.5, lat + 0.5, lon - 0.5, lon + 0.5))

        airports = cursor.fetchall()
        if not airports:
            return "N/A"

        # Find closest using simple distance squared
        closest_airport = min(
            airports,
            key=lambda port: (port[1] - lat)**2 + (port[2] - lon)**2
        )
        return closest_airport[0]
