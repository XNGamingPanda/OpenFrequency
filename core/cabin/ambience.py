import os
import time
import threading
try:
    import pygame
except ImportError:
    pygame = None

class AmbiencePlayer:
    """
    Handles background music and sound effects for the cabin.
    Uses pygame for audio mixing.
    """
    def __init__(self, config):
        self.config = config
        self.enabled = config.get('cabin', {}).get('ambience_enabled', True)
        self.bgm_volume = config.get('cabin', {}).get('bgm_volume', 0.5)
        self.sfx_volume = config.get('cabin', {}).get('sfx_volume', 0.6)
        
        self.is_initialized = False
        self._init_pygame()
        
    def _init_pygame(self):
        if not pygame:
            print("AmbiencePlayer: pygame not found. Audio disabled.")
            return

        try:
            pygame.mixer.init()
            pygame.mixer.set_num_channels(8) # Reserve channels
            self.channel_bgm = pygame.mixer.Channel(0)
            self.channel_sfx = pygame.mixer.Channel(1)
            self.channel_voice = pygame.mixer.Channel(2)
            self.is_initialized = True
            print("AmbiencePlayer: Initialized.")
        except Exception as e:
            print(f"AmbiencePlayer: Init failed: {e}")

    def play_bgm(self, filename):
        """Play background music (looping)."""
        if not self.is_initialized or not self.enabled: return
        
        path = self._resolve_path(filename, 'audio/boarding_music')
        if not path: return
        
        try:
            sound = pygame.mixer.Sound(path)
            self.channel_bgm.set_volume(self.bgm_volume)
            self.channel_bgm.play(sound, loops=-1, fade_ms=2000)
            print(f"AmbiencePlayer: Playing BGM {filename}")
        except Exception as e:
            print(f"AmbiencePlayer: Play BGM error: {e}")

    def stop_bgm(self, fade_ms=2000):
        if self.is_initialized:
            self.channel_bgm.fadeout(fade_ms)

    def play_sfx(self, filename, loops=0):
        """Play sound effect (e.g., chime, applause)."""
        if not self.is_initialized or not self.enabled: return
        
        path = self._resolve_path(filename, 'audio/sfx')
        if not path: return
        
        try:
            sound = pygame.mixer.Sound(path)
            self.channel_sfx.set_volume(self.sfx_volume)
            self.channel_sfx.play(sound, loops=loops)
        except Exception as e:
            print(f"AmbiencePlayer: Play SFX error: {e}")
            
    def play_announcement(self, path):
        """Play a pre-recorded announcement file."""
        if not self.is_initialized: return
        
        try:
            sound = pygame.mixer.Sound(path)
            self.channel_voice.set_volume(1.0) # Always loud
            # Duck BGM
            self.channel_bgm.set_volume(0.1)
            self.channel_voice.play(sound)
            
            # Restore BGM after
            # Ideally we need a callback, but for now simple ducking is ok
            # Or use a thread
            threading.Thread(target=self._unduck_bgm, args=(sound.get_length(),)).start()
            
        except Exception as e:
            print(f"AmbiencePlayer: Announcement error: {e}")

    def _unduck_bgm(self, delay):
        time.sleep(delay)
        if self.is_initialized:
            self.channel_bgm.set_volume(self.bgm_volume)

    def _resolve_path(self, filename, subfolder):
        # 1. Check absolute
        if os.path.exists(filename): return filename
        
        # 2. Check relative to data
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__))) # core/cabin/ -> root
        path = os.path.join(base_dir, 'data', subfolder, filename)
        if os.path.exists(path): return path
        
        print(f"AmbiencePlayer: File not found: {path}")
        return None
