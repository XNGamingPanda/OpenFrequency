"""
Visual Head Tracker - Uses MediaPipe Face Mesh for 0-cost TrackIR-like functionality.
"""
# cv2 and mediapipe are imported lazily in start() to prevent crash if not installed
import numpy as np
import threading
import time
from .context import event_bus


class OneEuroFilter:
    """Smoothing filter to reduce jitter in head tracking."""
    
    def __init__(self, freq=30.0, mincutoff=1.0, beta=0.007, dcutoff=1.0):
        self.freq = freq
        self.mincutoff = mincutoff
        self.beta = beta
        self.dcutoff = dcutoff
        self.x_prev = None
        self.dx_prev = 0.0
        self.t_prev = None
    
    def _smoothing_factor(self, cutoff):
        tau = 1.0 / (2 * np.pi * cutoff)
        te = 1.0 / self.freq
        return 1.0 / (1.0 + tau / te)
    
    def __call__(self, x, t=None):
        if self.x_prev is None:
            self.x_prev = x
            self.t_prev = t or time.time()
            return x
        
        t = t or time.time()
        dt = t - self.t_prev
        if dt <= 0:
            dt = 1.0 / self.freq
        self.freq = 1.0 / dt
        
        # Derivative
        edx = self._smoothing_factor(self.dcutoff)
        dx = (x - self.x_prev) / dt
        dx_hat = edx * dx + (1 - edx) * self.dx_prev
        
        # Cutoff based on speed
        cutoff = self.mincutoff + self.beta * abs(dx_hat)
        
        # Smoothed value
        ex = self._smoothing_factor(cutoff)
        x_hat = ex * x + (1 - ex) * self.x_prev
        
        self.x_prev = x_hat
        self.dx_prev = dx_hat
        self.t_prev = t
        
        return x_hat


class HeadTracker:
    """Visual head tracking using MediaPipe Face Mesh."""
    
    # 3D model points for a standard face (relative coordinates)
    MODEL_POINTS = np.array([
        (0.0, 0.0, 0.0),         # Nose tip
        (0.0, -63.6, -12.5),     # Chin
        (-43.3, 32.7, -26.0),    # Left eye corner
        (28.9, -28.9, -24.1),   # Left mouth corner
        (28.9, -28.9, -24.1)     # Right mouth corner
    ], dtype=np.float64)
    
    # Face mesh landmark indices for the 6 points
    LANDMARK_IDS = [1, 152, 33, 263, 61, 291]
    
    def __init__(self, config, socketio):
        self.config = config
        self.socketio = socketio
        self.enabled = config.get('head_tracking', {}).get('enabled', False)
        self.sensitivity = config.get('head_tracking', {}).get('sensitivity', 9.0)
        self.camera_index = config.get('head_tracking', {}).get('camera_index', 0)
        
        self.running = False
        self.thread = None
        self.cap = None
        self.mp_face_mesh = None
        self.face_mesh = None
        
        # Smoothing filters
        self.yaw_filter = OneEuroFilter()
        self.pitch_filter = OneEuroFilter()
        self.roll_filter = OneEuroFilter()
        
        # Current pose
        self.yaw = 0.0
        self.pitch = 0.0
        self.roll = 0.0
        
        # Camera calibration (approximate)
        self.camera_matrix = None
        self.dist_coeffs = np.zeros((4, 1))
        
        # Subscribe to config updates
        event_bus.on('config_updated', self._on_config_update)
        
        # Check for CV2 dependency
        try:
            import cv2
            self.cv2_available = True
        except ImportError:
            self.cv2_available = False
            print("HeadTracker: opencv-python (cv2) not found. Head tracking unavailable.")

    def _on_config_update(self, new_config):
        """Handle config changes."""
        new_enabled = new_config.get('head_tracking', {}).get('enabled', False)
        self.sensitivity = new_config.get('head_tracking', {}).get('sensitivity', 9.0)
        
        if new_enabled and not self.running:
            self.enabled = True
            self.start()
        elif not new_enabled and self.running:
            self.stop()
            self.enabled = False

    def start(self):
        """Start head tracking in background thread."""
        if self.running or not self.cv2_available:
            return
        
        try:
            import mediapipe as mp
            import cv2
            self.mp_face_mesh = mp.solutions.face_mesh
            self.face_mesh = self.mp_face_mesh.FaceMesh(
                max_num_faces=1,
                refine_landmarks=True,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5
            )
        except ImportError as e:
            print(f"HeadTracker: Missing dependency ({e}). Run: pip install mediapipe opencv-python")
            return
        
        self.cap = cv2.VideoCapture(self.camera_index)
        if not self.cap.isOpened():
            print(f"HeadTracker: Failed to open camera {self.camera_index}")
            return
        
        # Get camera resolution for calibration matrix
        w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.camera_matrix = np.array([
            [w, 0, w/2],
            [0, w, h/2],
            [0, 0, 1]
        ], dtype=np.float64)
        
        self.running = True
        self.thread = threading.Thread(target=self._tracking_loop, daemon=True)
        self.thread.start()
        
        print(f"HeadTracker: Started with camera {self.camera_index} ({w}x{h})")
    
    def stop(self):
        """Stop head tracking."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        if self.cap:
            self.cap.release()
        self.cap = None
        print("HeadTracker: Stopped")
    
    def _tracking_loop(self):
        """Main tracking loop at ~30fps."""
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.1)
                continue
            
            # Convert to RGB for MediaPipe
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self.face_mesh.process(rgb)
            
            if results.multi_face_landmarks:
                landmarks = results.multi_face_landmarks[0].landmark
                h, w = frame.shape[:2]
                
                # Extract 2D points for the 6 key landmarks
                image_points = np.array([
                    (landmarks[idx].x * w, landmarks[idx].y * h)
                    for idx in self.LANDMARK_IDS
                ], dtype=np.float64)
                
                # Solve PnP to get rotation
                success, rotation_vec, translation_vec = cv2.solvePnP(
                    self.MODEL_POINTS,
                    image_points,
                    self.camera_matrix,
                    self.dist_coeffs,
                    flags=cv2.SOLVEPNP_ITERATIVE
                )
                
                if success:
                    # Convert rotation vector to Euler angles
                    rotation_mat, _ = cv2.Rodrigues(rotation_vec)
                    pose_mat = np.hstack((rotation_mat, translation_vec))
                    _, _, _, _, _, _, euler = cv2.decomposeProjectionMatrix(pose_mat)
                    
                    raw_pitch = euler[0, 0]
                    raw_yaw = euler[1, 0]
                    raw_roll = euler[2, 0]
                    
                    # Apply smoothing
                    t = time.time()
                    self.pitch = self.pitch_filter(raw_pitch, t)
                    self.yaw = self.yaw_filter(raw_yaw, t)
                    self.roll = self.roll_filter(raw_roll, t)
                    
                    # Apply sensitivity curve (non-linear)
                    # Head 10° = Game 90° with sensitivity = 9
                    mapped_yaw = self.yaw * self.sensitivity
                    mapped_pitch = self.pitch * self.sensitivity
                    
                    # Emit to frontend and SimConnect
                    event_bus.emit('head_pose_update', {
                        'yaw': mapped_yaw,
                        'pitch': mapped_pitch,
                        'roll': self.roll,
                        'raw_yaw': self.yaw,
                        'raw_pitch': self.pitch
                    })
            
            # ~30fps
            time.sleep(0.033)
    
    def get_pose(self):
        """Get current head pose."""
        return {
            'yaw': self.yaw * self.sensitivity,
            'pitch': self.pitch * self.sensitivity,
            'roll': self.roll
        }
