"""
Patient Monitor - Detects medication intake using MediaPipe FaceMesh and Hands.

Detection logic:
    An intake event is counted when BOTH conditions are true simultaneously:
    - Mouth is open (lip distance / face height ratio exceeds threshold)
    - A fingertip is within a proximity radius of the mouth centre

    This conjunction prevents false positives from breathing or head movement,
    which was the main failure mode of the previous pose-based implementation.

Compliance statuses returned (matches decision_engine.py expected values):
    good      - at least 1 confirmed intake event detected
    acceptable - hand moved near mouth but no full intake event registered
    no_intake  - no relevant motion detected at all
"""

import cv2
import threading
import time
import numpy as np
import sys
from collections import deque
from typing import Dict, Any, Optional

import mediapipe as mp
from mediapipe.python.solutions import face_mesh as _mp_face_mod
from mediapipe.python.solutions import hands as _mp_hands_mod
from mediapipe.python.solutions import drawing_utils as _mp_draw


# FaceMesh landmark indices used for mouth and face height measurement
_MOUTH_UPPER = 13
_MOUTH_LOWER = 14
_FACE_TOP    = 10
_FACE_BOTTOM = 152

# Fingertip landmark indices (thumb through pinky)
_FINGERTIPS = [4, 8, 12, 16, 20]


class _IntakeDetector:
    """
    Stateful per-frame intake detector.

    Fires an intake event when mouth is open AND a fingertip is close to the mouth.
    A cooldown period prevents a single intake from being counted multiple times.
    """

    def __init__(self, mouth_open_ratio=0.04, proximity_ratio=0.18, cooldown_secs=2.5):
        # mouth_open_ratio: lip gap / face height threshold to consider mouth open
        # proximity_ratio:  fingertip must be within this fraction of frame size to mouth
        # cooldown_secs:    minimum gap between two separate intake events
        self.mouth_open_ratio = mouth_open_ratio
        self.proximity_ratio  = proximity_ratio
        self.cooldown_secs    = cooldown_secs
        
        self._face = _mp_face_mod.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._hands = _mp_hands_mod.Hands(
            max_num_hands=2,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

        self.intake_count      = 0
        self._intake_active    = False
        self._last_intake_time = 0.0
        # Smoothing buffer reduces jitter in mouth ratio measurement
        self._mouth_ratio_buf  = deque(maxlen=5)

        # Latest per-frame state read by the monitoring loop
        self.mouth_open       = False
        self.hands_near       = False
        self.intake_triggered = False

    @staticmethod
    def _px(lm, w, h):
        """Convert normalised landmark coordinates to pixel coordinates."""
        return int(lm.x * w), int(lm.y * h)

    def _mouth_ratio(self, face_lm, h, w):
        """Compute smoothed lip gap / face height ratio."""
        upper  = face_lm.landmark[_MOUTH_UPPER]
        lower  = face_lm.landmark[_MOUTH_LOWER]
        top    = face_lm.landmark[_FACE_TOP]
        bottom = face_lm.landmark[_FACE_BOTTOM]
        face_h = abs(bottom.y - top.y) or 1e-6
        ratio  = abs(lower.y - upper.y) / face_h
        self._mouth_ratio_buf.append(ratio)
        return float(np.mean(self._mouth_ratio_buf))

    def process_frame(self, frame: np.ndarray) -> np.ndarray:
        """
        Run detection on a BGR frame.
        Updates self.mouth_open, self.hands_near, self.intake_triggered, self.intake_count.
        Returns the annotated frame for optional display.
        """
        h, w = frame.shape[:2]
        rgb  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        mouth_open   = False
        mouth_center = None
        all_tips     = []

        # Face and mouth detection
        face_res = self._face.process(rgb)
        if face_res.multi_face_landmarks:
            fl    = face_res.multi_face_landmarks[0]
            ratio = self._mouth_ratio(fl, h, w)
            mouth_open = ratio > self.mouth_open_ratio

            ux, uy = self._px(fl.landmark[_MOUTH_UPPER], w, h)
            lx, ly = self._px(fl.landmark[_MOUTH_LOWER], w, h)
            mouth_center = ((ux + lx) // 2, (uy + ly) // 2)

            # Draw lip landmarks
            for idx in [_MOUTH_UPPER, _MOUTH_LOWER, 78, 308]:
                px, py = self._px(fl.landmark[idx], w, h)
                cv2.circle(frame, (px, py), 3, (0, 230, 255), -1)

            col = (0, 255, 136) if mouth_open else (255, 51, 102)
            cv2.circle(frame, mouth_center, 6, col, -1)
            thresh_px = int(self.proximity_ratio * min(w, h))
            cv2.circle(frame, mouth_center, thresh_px, (0, 229, 255), 1, cv2.LINE_AA)

        # Hand fingertip detection
        hand_res = self._hands.process(rgb)
        if hand_res.multi_hand_landmarks:
            for hand_lm in hand_res.multi_hand_landmarks:
                _mp_draw.draw_landmarks(
                    frame, hand_lm, _mp_hands_mod.HAND_CONNECTIONS,
                    _mp_draw.DrawingSpec(color=(40, 50, 70), thickness=1, circle_radius=1),
                    _mp_draw.DrawingSpec(color=(40, 50, 70), thickness=1),
                )
                for tip_idx in _FINGERTIPS:
                    tx, ty = self._px(hand_lm.landmark[tip_idx], w, h)
                    all_tips.append((tx, ty))
                    cv2.circle(frame, (tx, ty), 7, (255, 179, 0), -1)
                    cv2.circle(frame, (tx, ty), 7, (255, 255, 255), 1)
                    
        # Check if any fingertip is within proximity of mouth centre
        hands_near = False
        if mouth_center and all_tips:
            thresh_px = self.proximity_ratio * min(w, h)
            mx, my = mouth_center
            for tx, ty in all_tips:
                if np.hypot(tx - mx, ty - my) < thresh_px:
                    hands_near = True
                    break

        # Intake event: mouth open AND hand near, with cooldown
        now = time.time()
        triggered = False
        if mouth_open and hands_near:
            if not self._intake_active and (now - self._last_intake_time) > self.cooldown_secs:
                self.intake_count     += 1
                self._last_intake_time = now
                self._intake_active    = True
                triggered              = True
        else:
            self._intake_active = False

        self._draw_hud(frame, mouth_open, hands_near)

        self.mouth_open       = mouth_open
        self.hands_near       = hands_near
        self.intake_triggered = triggered

        return frame

    def _draw_hud(self, frame, mouth_open, hands_near):
        """Draw a small status overlay on the frame."""
        h, w = frame.shape[:2]
        overlay = frame.copy()
        cv2.rectangle(overlay, (8, 8), (240, 88), (13, 15, 20), -1)
        cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)

        col_m = (0, 255, 136) if mouth_open else (80, 80, 80)
        col_h = (0, 255, 136) if hands_near else (80, 80, 80)
        cv2.putText(frame, f"MOUTH OPEN : {'YES' if mouth_open else 'NO'}", (16, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, col_m, 1, cv2.LINE_AA)
        cv2.putText(frame, f"HAND NEAR  : {'YES' if hands_near else 'NO'}", (16, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, col_h, 1, cv2.LINE_AA)
        cv2.putText(frame, f"INTAKES    : {self.intake_count}", (16, 70),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 229, 255), 1, cv2.LINE_AA)

        if self.intake_triggered:
            cv2.rectangle(frame, (2, 2), (w - 2, h - 2), (0, 229, 255), 3)

    def release(self):
        """Close MediaPipe models and free resources."""
        self._face.close()
        self._hands.close()
        
class PatientMonitor:
    """
    Monitors patient medication intake using MediaPipe FaceMesh and Hands.

    Public interface used by main.py:
        start_monitoring(duration, callback) -> bool
        is_monitoring_active()               -> bool
        get_results()                        -> dict
        cleanup()

    The results dict uses compliance_status values that match decision_engine.py:
        good      - at least 1 confirmed intake event
        acceptable - hand near mouth but no full intake event
        no_intake  - no relevant motion at all
    """

    def __init__(self, config: dict, logger=None):
        self.config = config
        self.logger = logger
        self._log = (lambda msg: logger.info(msg)) if logger else print

        # device_id is at the top level of the patient_monitoring config section
        self._device_id = config.get("device_id", 0)
        self._fps       = config.get("fps", 20)

        # Optional MediaPipe threshold overrides via config.yaml patient_monitoring.mediapipe
        mp_cfg = config.get("mediapipe", {})
        self._mouth_open_ratio = mp_cfg.get("mouth_open_ratio", 0.04)
        self._proximity_ratio  = mp_cfg.get("proximity_ratio",  0.18)
        self._cooldown_secs    = mp_cfg.get("cooldown_secs",    2.5)

        self._active   = False
        self._thread: Optional[threading.Thread] = None
        self._results: Optional[dict] = None
        self._detector: Optional[_IntakeDetector] = None
        self._cap: Optional[cv2.VideoCapture] = None

        self._log("Patient monitor initialized with MediaPipe")

    def start_monitoring(self, duration: int = 30, callback=None) -> bool:
        """
        Start monitoring in a background thread.

        Args:
            duration: seconds to monitor
            callback: optional fn(detections, elapsed, duration) called approximately once per second
        Returns:
            True if started, False if already running or disabled
        """
        if self._active:
            self._log("PatientMonitor: already running")
            return False

        if not self.config.get("enabled", True):
            self._log("PatientMonitor: disabled in config")
            return False

        self._detector = _IntakeDetector(
            mouth_open_ratio=self._mouth_open_ratio,
            proximity_ratio=self._proximity_ratio,
            cooldown_secs=self._cooldown_secs,
        )
        self._results = None
        self._active  = True

        self._thread = threading.Thread(
            target=self._monitor_loop,
            args=(duration, callback),
            daemon=True,
            name="PatientMonitorThread",
        )
        self._thread.start()
        self._log(f"Patient monitoring started ({duration}s)")
        return True
        
    def is_monitoring_active(self) -> bool:
        """Returns True while the background monitoring thread is running."""
        return self._active

    def get_results(self) -> dict:
        """
        Return the monitoring result dict.
        Safe to call after is_monitoring_active() returns False.
        Returns a no_intake default if monitoring has not completed yet.
        """
        if self._results is None:
            return {
                "compliance_status": "no_intake",
                "swallow_count":     0,
                "cough_count":       0,
                "hand_motion_count": 0,
            }
        return self._results

    def cleanup(self):
        """Stop monitoring and release all camera and MediaPipe resources."""
        self._active = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._release_resources()
        self._log("Patient monitor cleanup complete")

    def _release_resources(self):
        if self._cap:
            self._cap.release()
            self._cap = None
        if self._detector:
            self._detector.release()
            self._detector = None

    def _monitor_loop(self, duration: int, callback):
        """Background thread: open camera, run detection, collect results."""
        cap = cv2.VideoCapture(self._device_id)
        if not cap.isOpened():
            self._log(f"PatientMonitor: cannot open camera (device {self._device_id})")
            self._active  = False
            self._results = self._build_result(0, 0)
            return

        self._cap = cap
        cap.set(cv2.CAP_PROP_FPS, self._fps)

        # Warm up the camera - Pi USB cameras need a few seconds before producing valid frames
        self._log("PatientMonitor: warming up camera...")
        warm_start = time.time()
        while time.time() - warm_start < 3.0:
            cap.read()

        start_time      = time.time()
        hand_motion_cnt = 0
        frame_interval  = 1.0 / self._fps
        next_frame_time = time.time()

        self._log("PatientMonitor: monitoring loop started")
        
        try:
            while self._active:
                elapsed = time.time() - start_time
                if elapsed >= duration:
                    break

                now = time.time()
                if now < next_frame_time:
                    time.sleep(max(0, next_frame_time - now))
                next_frame_time += frame_interval

                ret, frame = cap.read()
                if not ret:
                    continue

                frame = cv2.flip(frame, 1)
                self._detector.process_frame(frame)

                if self._detector.hands_near:
                    hand_motion_cnt += 1

                # Fire callback approximately once per second
                if callback and int(elapsed) != int(elapsed - frame_interval):
                    detections = {
                        "swallow_count":    self._detector.intake_count,
                        "hand_motion":      self._detector.hands_near,
                        "mouth_open":       self._detector.mouth_open,
                    }
                    try:
                        callback(detections, elapsed, duration)
                    except Exception as cb_err:
                        self._log(f"PatientMonitor callback error: {cb_err}")

        except Exception as e:
            self._log(f"PatientMonitor: error in monitoring loop: {e}")
        finally:
            intake_count = self._detector.intake_count if self._detector else 0
            self._results = self._build_result(intake_count, hand_motion_cnt)
            self._release_resources()
            self._active = False
            self._log(
                f"Patient monitoring finished: "
                f"intakes={intake_count}, "
                f"status={self._results['compliance_status']}"
            )

    def _build_result(self, intake_count: int, hand_motion_count: int) -> dict:
        """
        Map raw counts to compliance_status values expected by decision_engine.py.

        good       - at least 1 confirmed intake (mouth open + hand near simultaneously)
        acceptable - hand moved near mouth but no full intake registered
        no_intake  - no relevant motion detected
        """
        if intake_count >= 1:
            status = "good"
        elif hand_motion_count > 0:
            status = "acceptable"
        else:
            status = "no_intake"

        return {
            "compliance_status": status,
            # swallow_count maps to intake events for decision engine compatibility
            "swallow_count":     intake_count,
            # cough detection is not used in the new approach - always 0
            "cough_count":       0,
            "hand_motion_count": hand_motion_count,
        }
        
# Standalone test - run directly to verify detection with a live camera feed
if __name__ == "__main__":
    DURATION  = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
    DEVICE_ID = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    print("=" * 55)
    print("  PatientMonitor standalone test")
    print(f"  Camera  : /dev/video{DEVICE_ID}")
    print(f"  Duration: {'unlimited' if DURATION == 9999 else str(DURATION) + 's'}  (Q/ESC to stop)")
    print("  R = reset counter")
    print("=" * 55)

    detector = _IntakeDetector(
        mouth_open_ratio=0.04,
        proximity_ratio=0.18,
        cooldown_secs=2.5,
    )

    cap = cv2.VideoCapture(DEVICE_ID)
    if not cap.isOpened():
        print(f"ERROR: Cannot open camera {DEVICE_ID}")
        sys.exit(1)

    cap.set(cv2.CAP_PROP_FPS, 30)
    start = time.time()
    print("\nLive feed open. Monitoring...\n")

    while True:
        elapsed = time.time() - start
        if elapsed >= DURATION:
            break

        ret, frame = cap.read()
        if not ret:
            continue

        frame = cv2.flip(frame, 1)
        annotated = detector.process_frame(frame)

        h, w = annotated.shape[:2]
        if DURATION < 9999:
            bar_w = int(w * min(elapsed / DURATION, 1.0))
            cv2.rectangle(annotated, (0, h - 6), (bar_w, h), (0, 229, 255), -1)
            cv2.putText(annotated, f"{max(0, DURATION - elapsed):.0f}s remaining",
                        (w - 135, h - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (100, 100, 100), 1, cv2.LINE_AA)

        cv2.imshow("PatientMonitor test  (Q=quit  R=reset)", annotated)

        key = cv2.waitKey(33) & 0xFF
        if key in (ord('q'), ord('Q'), 27):
            break
        if key in (ord('r'), ord('R')):
            detector.intake_count = 0
            print("  Counter reset to 0")

        if detector.intake_triggered:
            print(f"  INTAKE #{detector.intake_count}  (t={elapsed:.1f}s)")

    cap.release()
    cv2.destroyAllWindows()
    detector.release()
    
    intake_count = detector.intake_count
    status = "good" if intake_count >= 1 else ("acceptable" if detector.hands_near else "no_intake")

    print("\n" + "=" * 55)
    print("  RESULTS")
    print("=" * 55)
    print(f"  Duration monitored : {time.time() - start:.1f}s")
    print(f"  Intakes detected   : {intake_count}")
    print(f"  Compliance status  : {status.upper()}")
    print("=" * 55)
