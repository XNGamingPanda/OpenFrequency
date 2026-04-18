"""
Hoppie ACARS client — HTTP interface to https://www.hoppie.nl/acars/
Handles logon, polling, and message dispatch via the public Hoppie network.
"""
import threading
import time
import urllib.parse
import urllib.request
from typing import Optional

HOPPIE_URL = 'https://www.hoppie.nl/acars/system/connect.html'
POLL_INTERVAL = 30  # seconds


class HoppieClient:
    def __init__(self):
        self.logon_code: str = ''
        self.callsign: str = ''
        self.connected: bool = False
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._socketio = None

    def _post(self, params: dict) -> str:
        params['logon'] = self.logon_code
        params['from'] = self.callsign
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(HOPPIE_URL, data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode('utf-8', errors='replace').strip()

    def logon(self, logon_code: str, callsign: str) -> bool:
        self.logon_code = logon_code
        self.callsign = callsign.upper()
        try:
            resp = self._post({'to': 'SERVER', 'type': 'ping', 'packet': ''})
            self.connected = resp.startswith('ok')
            return self.connected
        except Exception as e:
            print(f'Hoppie logon error: {e}')
            self.connected = False
            return False

    def logoff(self):
        self._stop_event.set()
        self.connected = False

    def send_telex(self, to: str, text: str) -> bool:
        try:
            resp = self._post({'to': to, 'type': 'telex', 'packet': text})
            return resp.startswith('ok')
        except Exception as e:
            print(f'Hoppie send error: {e}')
            return False

    def poll(self) -> list:
        try:
            resp = self._post({'to': 'SERVER', 'type': 'poll', 'packet': ''})
            if not resp.startswith('ok'):
                return []
            # Parse: ok {FROM TELEX {MESSAGE}} {FROM TELEX {MESSAGE}} ...
            messages = []
            import re
            for m in re.finditer(r'\{(\S+)\s+(\S+)\s+\{([^}]*)\}\}', resp):
                messages.append({'from': m.group(1), 'type': m.group(2), 'packet': m.group(3)})
            return messages
        except Exception as e:
            print(f'Hoppie poll error: {e}')
            return []

    def start_polling(self, socketio):
        self._socketio = socketio
        self._stop_event.clear()
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def _poll_loop(self):
        while not self._stop_event.is_set() and self.connected:
            time.sleep(POLL_INTERVAL)
            if self._stop_event.is_set():
                break
            msgs = self.poll()
            for msg in msgs:
                if self._socketio:
                    self._socketio.emit('hoppie_message', {
                        'dir': 'in',
                        'from': msg['from'],
                        'to': self.callsign,
                        'packet': msg['packet'],
                        'ts': time.time(),
                    })


hoppie_client = HoppieClient()
