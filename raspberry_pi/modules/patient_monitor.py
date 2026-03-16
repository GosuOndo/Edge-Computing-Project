"""
Smart Medication System - Patient Monitoring Module

Uses MediaPipe to monitor patient behavior during medication intake.
Detects swallowing, coughing, and proper intake patterns.

This is a CRITICAL module addressing the professor's requirement for
behavioral monitoring during and after medication intake.
"""

import cv2
import mediapipe as mp
import numpy as np
import time
from typing import Dict, Any, Optional
from collections import deque
from threading import Thread, Event


class PatientMonitor:
    """
    Patient behavior monitoring using MediaPipe

    Monitors:
    - Swallowing motion (head tilt detection)
    - Coughing/gagging (mouth opening detection)
    - Hand-to-mouth motion (pill intake confirmation)
    - Overall compliance behavior
    """

    def __init__(self, config: dict, logger):
        """
        Initialize patient monitor

        Args:
            config: Patient monitoring configuration
            logger: Logger instance
        """
        self.config = config
        self.logger = logger

        # Camera configuration
        self.camera_device = config.get('device_id', 0)
        self.fps = config.get('fps', 20)
        self.enabled = config.get('enabled', True)

        # Monitoring configuration
        self.monitoring_duration = config.get('duration_seconds', 30)

        # Detection configuration
        detection_config = config.get('detection', {})
        self.swallow_enabled = detection_config.get('swallow', {}).get('enabled', True)
        self.swallow_sensitivity = detection_config.get('swallow', {}).get('sensitivity', 0.85)
        self.cough_enabled = detection_config.get('cough', {}).get('enabled', True)
        self.cough_sensitivity = detection_config.get('cough', {}).get('sensitivity', 0.80)
        self.hand_motion_enabled = detection_config.get('hand_motion', {}).get('enabled', True)

        # MediaPipe initialization
        self.mp_face_mesh = mp.solutions.face_mesh
        self.mp_pose = mp.solutions.pose
        self.mp_drawing = mp.solutions.drawing_utils

        self.face_mesh = None
        self.pose = None

        # Camera
        self.camera = None
        self.camera_ready = False

        # Monitoring state
        self.is_monitoring = False
        self.monitoring_thread = None
        self.stop_event = Event()

        # Detection history
        self.swallow_history = deque(maxlen=30)
        self.mouth_opening_history = deque(maxlen=30)
        self.hand_position_history = deque(maxlen=30)

        # Results
        self.monitoring_results = {}

        self.logger.info("Patient monitor initialized with MediaPipe")

    def initialize_camera(self) -> bool:
        """
        Initialize camera for monitoring

        Returns:
            True if successful
        """
        try:
            if self.camera is not None and self.camera_ready:
                return True

            self.camera = cv2.VideoCapture(self.camera_device)
            
            if not self.camera.isOpened():
                self.logger.error("Failed to open camera for patient monitoring")
                self.camera = None
                return False

            self.camera.set(cv2.CAP_PROP_FPS, self.fps)

            ret, frame = self.camera.read()
            if not ret or frame is None:
                self.logger.error("Failed to capture test frame")
                self.release_camera()
                return False

            self.camera_ready = True
            self.logger.info(f"Camera ready for patient monitoring ({self.fps} FPS)")
            return True

        except Exception as e:
            self.logger.error(f"Camera initialization failed: {e}")
            self.release_camera()
            return False

    def release_camera(self):
        """Release camera resources"""
        if self.camera:
            try:
                self.camera.release()
            except Exception as e:
                self.logger.debug(f"Camera release warning: {e}")
            finally:
                self.camera = None
                self.camera_ready = False
                self.logger.info("Camera released")

    def initialize_mediapipe(self):
        """Initialize MediaPipe models"""
        try:
            if self.face_mesh is None:
                self.face_mesh = self.mp_face_mesh.FaceMesh(
                    max_num_faces=1,
                    refine_landmarks=True,
                    min_detection_confidence=0.5,
                    min_tracking_confidence=0.5
                )

            if self.pose is None:
                self.pose = self.mp_pose.Pose(
                    min_detection_confidence=0.5,
                    min_tracking_confidence=0.5
                )

            self.logger.info("MediaPipe models initialized")

        except Exception as e:
            self.logger.error(f"MediaPipe initialization failed: {e}")
            raise

    def cleanup_mediapipe(self):
        """Cleanup MediaPipe resources"""
        if self.face_mesh:
            try:
                self.face_mesh.close()
            except Exception as e:
                self.logger.debug(f"Face mesh cleanup warning: {e}")
            finally:
                self.face_mesh = None

        if self.pose:
            try:
                self.pose.close()
            except Exception as e:
                self.logger.debug(f"Pose cleanup warning: {e}")
            finally:
                self.pose = None

        self.logger.info("MediaPipe resources released")

    def detect_swallowing(self, face_landmarks) -> Optional[float]:
        """Detect swallowing motion via head tilt"""
        if not face_landmarks or not self.swallow_enabled:
            return None

        try:
            nose = face_landmarks.landmark[1]
            chin = face_landmarks.landmark[152]

            vertical_dist = abs(nose.y - chin.y)
            self.swallow_history.append(vertical_dist)

            if len(self.swallow_history) < 10:
                return None

            recent_avg = np.mean(list(self.swallow_history)[-5:])
            baseline_avg = np.mean(list(self.swallow_history)[:5])

            tilt_change = baseline_avg - recent_avg
            confidence = min(max(tilt_change * 10, 0.0), 1.0)

            if confidence >= self.swallow_sensitivity:
                return confidence

            return None

        except Exception as e:
            self.logger.debug(f"Swallow detection error: {e}")
            return None

    def detect_coughing(self, face_landmarks) -> Optional[float]:
        """Detect coughing/gagging via sudden mouth opening"""
        if not face_landmarks or not self.cough_enabled:
            return None

        try:
            upper_lip = face_landmarks.landmark[13]
            lower_lip = face_landmarks.landmark[14]

            mouth_opening = abs(upper_lip.y - lower_lip.y)
            self.mouth_opening_history.append(mouth_opening)

            if len(self.mouth_opening_history) < 10:
                return None

            recent_max = max(list(self.mouth_opening_history)[-5:])
            baseline_avg = np.mean(list(self.mouth_opening_history)[:-5])

            opening_change = recent_max - baseline_avg
            confidence = min(max(opening_change * 20, 0.0), 1.0)

            if confidence >= self.cough_sensitivity:
                return confidence

            return None

        except Exception as e:
            self.logger.debug(f"Cough detection error: {e}")
            return None

    def detect_hand_to_mouth(self, pose_landmarks, face_landmarks) -> Optional[float]:
        """Detect hand-to-mouth motion"""
        if not pose_landmarks or not face_landmarks or not self.hand_motion_enabled:
            return None

        try:
            nose = face_landmarks.landmark[1]
            mouth_pos = np.array([nose.x, nose.y])

            right_hand = pose_landmarks.landmark[self.mp_pose.PoseLandmark.RIGHT_WRIST]
            left_hand = pose_landmarks.landmark[self.mp_pose.PoseLandmark.LEFT_WRIST]

            right_hand_pos = np.array([right_hand.x, right_hand.y])
            left_hand_pos = np.array([left_hand.x, left_hand.y])

            right_dist = np.linalg.norm(right_hand_pos - mouth_pos)
            left_dist = np.linalg.norm(left_hand_pos - mouth_pos)

            min_dist = min(right_dist, left_dist)
            self.hand_position_history.append(min_dist)

            if len(self.hand_position_history) < 10:
                return None

            if min_dist < 0.15:
                confidence = 1.0 - (min_dist / 0.15)
                return confidence

            return None

        except Exception as e:
            self.logger.debug(f"Hand motion detection error: {e}")
            return None

    def process_frame(self, frame: np.ndarray) -> Dict[str, Any]:
        """
        Process a single frame for behavior detection
        """
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        face_results = self.face_mesh.process(rgb_frame)
        pose_results = self.pose.process(rgb_frame)

        detections = {
            'swallow_detected': False,
            'swallow_confidence': 0.0,
            'cough_detected': False,
            'cough_confidence': 0.0,
            'hand_motion_detected': False,
            'hand_motion_confidence': 0.0,
            'timestamp': time.time()
        }
        
        if face_results.multi_face_landmarks:
            face_landmarks = face_results.multi_face_landmarks[0]

            swallow_conf = self.detect_swallowing(face_landmarks)
            if swallow_conf:
                detections['swallow_detected'] = True
                detections['swallow_confidence'] = swallow_conf

            cough_conf = self.detect_coughing(face_landmarks)
            if cough_conf:
                detections['cough_detected'] = True
                detections['cough_confidence'] = cough_conf

            if pose_results.pose_landmarks:
                hand_conf = self.detect_hand_to_mouth(
                    pose_results.pose_landmarks,
                    face_landmarks
                )
                if hand_conf:
                    detections['hand_motion_detected'] = True
                    detections['hand_motion_confidence'] = hand_conf

        return detections

    def _monitoring_loop(self, duration: int, callback: Optional[callable] = None):
        """
        Main monitoring loop (runs in thread)
        """
        self.logger.info(f"Starting patient monitoring ({duration}s window)")

        detections_log = []
        swallow_count = 0
        cough_count = 0
        hand_motion_count = 0
        start_time = time.time()
        frame_count = 0
        elapsed = 0.0

        try:
            while not self.stop_event.is_set():
                elapsed = time.time() - start_time
                if elapsed >= duration:
                    break

                if not self.camera or not self.camera_ready:
                    self.logger.warning("Camera not ready during monitoring")
                    break

                ret, frame = self.camera.read()
                if not ret or frame is None:
                    self.logger.warning("Failed to capture frame during monitoring")
                    time.sleep(0.05)
                    continue

                detections = self.process_frame(frame)
                detections_log.append(detections)

                if detections['swallow_detected']:
                    swallow_count += 1
                if detections['cough_detected']:
                    cough_count += 1
                if detections['hand_motion_detected']:
                    hand_motion_count += 1

                if callback and not self.stop_event.is_set():
                    try:
                        callback(detections, elapsed, duration)
                    except Exception as e:
                        self.logger.error(f"Monitoring callback error: {e}")

                frame_count += 1
                time.sleep(max(0.01, 1.0 / self.fps))

            self.monitoring_results = {
                'duration_seconds': elapsed,
                'frames_processed': frame_count,
                'fps_actual': frame_count / elapsed if elapsed > 0 else 0,
                'swallow_detected': swallow_count > 0,
                'swallow_count': swallow_count,
                'swallow_frames': sum(1 for d in detections_log if d['swallow_detected']),
                'cough_detected': cough_count > 0,
                'cough_count': cough_count,
                'cough_frames': sum(1 for d in detections_log if d['cough_detected']),
                'hand_motion_detected': hand_motion_count > 0,
                'hand_motion_count': hand_motion_count,
                'hand_motion_frames': sum(1 for d in detections_log if d['hand_motion_detected']),
                'compliance_status': self._assess_compliance(swallow_count, cough_count, hand_motion_count),
                'detections_log': detections_log,
                'timestamp': time.time()
            }

            self.logger.info(
                f"Monitoring complete: "
                f"Swallows={swallow_count}, Coughs={cough_count}, "
                f"Hand motions={hand_motion_count}"
            )
            
        except Exception as e:
            self.logger.error(f"Monitoring loop failed: {e}")
            self.monitoring_results = {
                'duration_seconds': elapsed,
                'frames_processed': frame_count,
                'fps_actual': frame_count / elapsed if elapsed > 0 else 0,
                'swallow_detected': False,
                'swallow_count': 0,
                'swallow_frames': 0,
                'cough_detected': False,
                'cough_count': 0,
                'cough_frames': 0,
                'hand_motion_detected': False,
                'hand_motion_count': 0,
                'hand_motion_frames': 0,
                'compliance_status': 'no_intake',
                'detections_log': detections_log,
                'timestamp': time.time(),
                'error': str(e)
            }

        finally:
            self.is_monitoring = False
            self.logger.info("Patient monitoring stopped")

    def _assess_compliance(self, swallow_count: int, cough_count: int, hand_motion_count: int) -> str:
        """
        Assess overall compliance based on detections
        """
        if swallow_count > 0 and hand_motion_count > 0 and cough_count == 0:
            return "good"

        if swallow_count > 0 and cough_count <= 2:
            return "acceptable"

        if cough_count > 5:
            return "concerning"

        if swallow_count == 0 and hand_motion_count == 0:
            return "no_intake"

        return "unclear"

    def start_monitoring(self, duration: int = None, callback: Optional[callable] = None) -> bool:
        """
        Start patient behavior monitoring
        """
        if self.is_monitoring:
            self.logger.warning("Monitoring already in progress")
            return False

        if not self.enabled:
            self.logger.warning("Patient monitoring is disabled")
            return False

        if not self.camera_ready:
            if not self.initialize_camera():
                return False

        if not self.face_mesh or not self.pose:
            self.initialize_mediapipe()

        self.monitoring_results = {}
        self.swallow_history.clear()
        self.mouth_opening_history.clear()
        self.hand_position_history.clear()

        if duration is None:
            duration = self.monitoring_duration

        self.is_monitoring = True
        self.stop_event.clear()
        self.monitoring_thread = Thread(
            target=self._monitoring_loop,
            args=(duration, callback),
            daemon=True
        )
        self.monitoring_thread.start()

        self.logger.info(f"Patient monitoring started ({duration}s)")
        return True

    def stop_monitoring(self):
        """Stop patient monitoring"""
        if not self.is_monitoring:
            return

        self.stop_event.set()

        if self.monitoring_thread and self.monitoring_thread.is_alive():
            self.monitoring_thread.join(timeout=2)

        if self.monitoring_thread and self.monitoring_thread.is_alive():
            self.logger.warning("Monitoring thread did not stop within timeout")

        self.is_monitoring = False
        
    def get_results(self) -> Dict[str, Any]:
        """Get monitoring results"""
        return self.monitoring_results.copy()

    def is_monitoring_active(self) -> bool:
        """Check if monitoring is currently active"""
        return self.is_monitoring

    def cleanup(self):
        """Cleanup all resources"""
        self.stop_monitoring()
        self.release_camera()
        self.cleanup_mediapipe()
        self.logger.info("Patient monitor cleanup complete")
