import os
import json
import secrets
import time
from threading import RLock

class AuthManager:
    """
    Manages security modes, trusted tokens, and ban lists.
    Data is stored in the main config.json under the 'security' key.
    
    Token types:
    - TRUSTED: Saved to config.json, full permissions
    - GUEST: In-memory only, limited permissions (can't modify config)
    """
    def __init__(self, config, config_path='config.json'):
        self.lock = RLock()
        self.config = config  # Reference to the shared config dict
        self.config_path = config_path
        
        # Ensure security section exists with defaults
        if 'security' not in self.config:
            self.config['security'] = {
                "mode": "doorbell",
                "trusted_tokens": {},
                "banned_ips": [],
                "history": []
            }
        
        # Shortcut reference
        self.data = self.config['security']
        
        # Runtime tracking (not saved to disk)
        self.pending_requests = {}  # {sid: {"ip": ip, "ua": ua, "ts": ts}}
        self.temp_tokens = {}  # {token: {"ip": ip, "device": ua, "created_at": ts}}
        self.token_sessions = {}  # {token: [sid1, sid2, ...]} - Track socket sessions per token
        
        print(f"AuthManager: Initialized. Mode={self.data.get('mode', 'doorbell')}")

    def save(self):
        """Save entire config to disk."""
        with self.lock:
            try:
                with open(self.config_path, 'w', encoding='utf-8') as f:
                    json.dump(self.config, f, indent=2, ensure_ascii=False)
                print(f"AuthManager: Saved config to {self.config_path}")
            except Exception as e:
                print(f"AuthManager: FAILED TO SAVE to {self.config_path}: {e}")

    def is_trusted(self, token):
        """Check if a token is valid (trusted or temp)."""
        return token in self.data.get('trusted_tokens', {}) or token in self.temp_tokens

    def is_persistent_token(self, token):
        """Check if token is a persistent (trusted) token."""
        return token in self.data.get('trusted_tokens', {})

    def is_banned(self, ip):
        return ip in self.data.get('banned_ips', [])

    def is_localhost(self, ip):
        return ip in ['127.0.0.1', '::1', 'localhost']

    def get_permission_level(self, ip, token):
        """
        Returns permission level based on token's actual permissions setting:
        - 'ADMIN': localhost, full access
        - 'FULL': token with 'full' permissions
        - 'READONLY': token with 'readonly' permissions
        - 'NONE': no access
        """
        if self.is_localhost(ip):
            return 'ADMIN'
        if self.is_banned(ip):
            return 'NONE'
        if token:
            perm = self.get_token_permissions(token)
            if perm == 'full':
                return 'FULL'
            elif perm == 'readonly':
                return 'READONLY'
        return 'NONE'

    def check_access(self, ip, token):
        """
        Determines access status for a request.
        Returns: 'ALLOW_ADMIN', 'ALLOW', 'ALLOW_GUEST', 'WAIT', 'BLOCK'
        """
        # 1. Localhost always Admin
        if self.is_localhost(ip):
            return 'ALLOW_ADMIN'

        # 2. Blacklist check
        if self.is_banned(ip):
            return 'BLOCK'

        # 3. Token check - distinguish between trusted and temp
        if token:
            if self.is_persistent_token(token):
                return 'ALLOW'
            if token in self.temp_tokens:
                return 'ALLOW_GUEST'

        # 4. Mode Logic
        mode = self.data.get('mode', 'doorbell')
        
        if mode == 'open':
            return 'ALLOW_GUEST'
        elif mode == 'lockdown':
            return 'BLOCK'
        else:  # doorbell
            return 'WAIT'

    def create_token(self, ip, device_info, persistent=False):
        """
        Generates a new token.
        - persistent=True: Saved to config.json, default 'full' permissions
        - persistent=False: In-memory only, default 'readonly' permissions
        """
        token = secrets.token_urlsafe(32)
        token_data = {
            "ip": ip,
            "device": device_info,
            "created_at": time.time(),
            "persistent": persistent,
            "permissions": "full" if persistent else "readonly"
        }
        
        with self.lock:
            if persistent:
                # Save to config
                if 'trusted_tokens' not in self.data:
                    self.data['trusted_tokens'] = {}
                self.data['trusted_tokens'][token] = token_data
                self.save()
                print(f"AuthManager: Created TRUSTED token for {ip} (full permissions)")
            else:
                # Keep in memory only
                self.temp_tokens[token] = token_data
                print(f"AuthManager: Created TEMP token for {ip} (readonly permissions)")
        
        return token
    
    def update_token_permissions(self, token, permissions):
        """Update permissions for a token. Returns True on success."""
        if permissions not in ['full', 'readonly']:
            return False
        
        with self.lock:
            # Check trusted tokens
            if token in self.data.get('trusted_tokens', {}):
                self.data['trusted_tokens'][token]['permissions'] = permissions
                self.save()
                return True
            # Check temp tokens
            elif token in self.temp_tokens:
                self.temp_tokens[token]['permissions'] = permissions
                return True
        return False
    
    def get_token_permissions(self, token):
        """Get permissions for a token. Returns 'readonly' as default."""
        if token in self.data.get('trusted_tokens', {}):
            return self.data['trusted_tokens'][token].get('permissions', 'full')
        if token in self.temp_tokens:
            return self.temp_tokens[token].get('permissions', 'readonly')
        return 'none'

    def register_session(self, token, sid):
        """Register a socket session with a token."""
        if token not in self.token_sessions:
            self.token_sessions[token] = []
        if sid not in self.token_sessions[token]:
            self.token_sessions[token].append(sid)

    def unregister_session(self, sid):
        """Remove a session from tracking (on disconnect)."""
        for token in list(self.token_sessions.keys()):
            if sid in self.token_sessions[token]:
                self.token_sessions[token].remove(sid)
            if not self.token_sessions[token]:
                del self.token_sessions[token]

    def revoke_token(self, token):
        """Revoke a token. Returns list of affected session IDs to force logout."""
        affected_sessions = []
        with self.lock:
            # Get affected sessions before deleting
            if token in self.token_sessions:
                affected_sessions = self.token_sessions[token].copy()
                del self.token_sessions[token]
            
            # Check trusted tokens
            if token in self.data.get('trusted_tokens', {}):
                del self.data['trusted_tokens'][token]
                self.save()
            # Check temp tokens
            elif token in self.temp_tokens:
                del self.temp_tokens[token]
        
        return affected_sessions

    def ban_ip(self, ip):
        with self.lock:
            if 'banned_ips' not in self.data:
                self.data['banned_ips'] = []
            if ip not in self.data['banned_ips']:
                self.data['banned_ips'].append(ip)
                # Also revoke any tokens from this IP
                tokens_to_remove = [k for k, v in self.data.get('trusted_tokens', {}).items() if v.get('ip') == ip]
                for t in tokens_to_remove:
                    del self.data['trusted_tokens'][t]
                # Also remove temp tokens
                temp_to_remove = [k for k, v in self.temp_tokens.items() if v.get('ip') == ip]
                for t in temp_to_remove:
                    del self.temp_tokens[t]
                self.save()

    def unban_ip(self, ip):
        with self.lock:
            if ip in self.data.get('banned_ips', []):
                self.data['banned_ips'].remove(ip)
                self.save()

    def set_mode(self, mode):
        if mode not in ['open', 'doorbell', 'lockdown']:
            return False
        with self.lock:
            self.data['mode'] = mode
            self.save()
        return True
