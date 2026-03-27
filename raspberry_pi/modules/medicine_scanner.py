"""
Smart Medication System - Medicine Scanner Module

Performs OCR (Optical Character Recognition) on medicine labels using
Tesseract. Includes preprocessing pipeline for improved accuracy.
"""

import cv2
import numpy as np
import pytesseract
from PIL import Image
import time
from contextlib import nullcontext
from typing import Dict, Any, Optional, List
import re

try:
    from raspberry_pi.utils.profiler import profile_stage
except ImportError:  # pragma: no cover - script execution path
    from utils.profiler import profile_stage


class MedicineScanner:
    """OCR-based medicine label scanner"""
    
    def __init__(self, config: dict, logger):
        """
        Initialize medicine scanner
        
        Args:
            config: OCR configuration dictionary
            logger: Logger instance
        """
        self.config = config
        self.logger = logger
        
        # Camera configuration
        self.camera_device = config.get('device_id', 0)
        self.resolution = tuple(config.get('resolution', [640, 480]))
        
        # OCR configuration
        self.ocr_language = config.get('language', 'eng')
        self.ocr_config = config.get('config', '--psm 6')
        self.min_confidence = config.get('min_confidence', 0.75)
        
        # Preprocessing flags
        preprocess = config.get('preprocessing', {})
        self.enable_grayscale = preprocess.get('grayscale', True)
        self.enable_denoise = preprocess.get('denoise', True)
        self.enable_threshold = preprocess.get('threshold', True)
        
        # Camera
        self.camera = None
        self.camera_ready = False
        self._profiler_context = None
        
        # Known medicine database (for fuzzy matching)
        self.known_medicines = []
        
        self.logger.info(f"Medicine scanner initialized (camera: {self.camera_device})")

    def set_profiler_context(self, profiler, run_id: str, scenario: str, station_id: str):
        self._profiler_context = {
            "profiler": profiler,
            "run_id": run_id,
            "scenario": scenario,
            "station_id": station_id,
        }

    def clear_profiler_context(self):
        self._profiler_context = None

    def _profile_stage(self, stage: str, notes=None):
        if not self._profiler_context or not self._profiler_context.get("profiler"):
            return nullcontext()

        ctx = self._profiler_context
        return profile_stage(
            ctx["profiler"],
            ctx["run_id"],
            ctx["scenario"],
            ctx["station_id"],
            stage,
            notes,
        )
    
    def initialize_camera(self) -> bool:
        """
        Initialize camera

        Returns:
            True if successful
        """
        frame_means = []
        with self._profile_stage(
            "camera_init",
            notes=lambda: {
                "camera_device": self.camera_device,
                "camera_ready": self.camera_ready,
                "warmup_frames": len(frame_means),
                "frame_means": frame_means,
            },
        ):
            try:
                self.camera = cv2.VideoCapture(self.camera_device)

                if not self.camera.isOpened():
                    self.logger.error(f"Failed to open camera device {self.camera_device}")
                    return False

                # Set resolution
                self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
                self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])

                # Warm up webcam
                time.sleep(4.0)

                # Try several frames, but do NOT fail just because they are dark
                ret = False
                frame = None

                for _ in range(20):
                    ret, frame = self.camera.read()

                    if ret and frame is not None:
                        frame_means.append(round(float(frame.mean()), 2))
                    else:
                        frame_means.append(None)

                    time.sleep(0.1)

                self.logger.info(f"Camera init warm-up frame means: {frame_means}")

                if not ret or frame is None:
                    self.logger.error("Failed to capture test frame")
                    return False

                # Do not hard-fail on dark frame; only warn
                if frame.mean() <= 5:
                    self.logger.warning("Camera frame appears dark/empty, but continuing")

                self.camera_ready = True
                self.logger.info(
                    f"Camera initialized at {self.resolution[0]}x{self.resolution[1]} "
                    f"(device {self.camera_device})"
                )
                return True

            except Exception as e:
                self.logger.error(f"Camera initialization failed: {e}")
                return False
    

    
    def release_camera(self):
        """Release camera resources"""
        with self._profile_stage(
            "camera_release",
            notes=lambda: {
                "camera_device": self.camera_device,
                "camera_ready_before_release": self.camera_ready,
            },
        ):
            if self.camera:
                self.camera.release()
                self.camera = None
                self.camera_ready = False
                self.logger.info("Camera released")
            

    def capture_frame(self) -> Optional[np.ndarray]:
        """
        Capture a single frame from camera

        Returns:
            Frame as numpy array or None if failed
        """
        frame = None

        with self._profile_stage(
            "frame_capture",
            notes=lambda: {
                "camera_ready": self.camera_ready,
                "captured": frame is not None,
                "frame_mean": (
                    round(float(frame.mean()), 2)
                    if frame is not None else None
                ),
            },
        ):
            if not self.camera_ready:
                self.logger.warning("Camera not initialized")
                return None

            ret, frame = self.camera.read()

            if not ret or frame is None:
                self.logger.error("Failed to capture frame")
                return None

            if frame.mean() <= 5:
                self.logger.warning("Captured frame is nearly black")

            return frame
    
    def preprocess_image(self, image: np.ndarray) -> np.ndarray:
        """
        Preprocess image for better OCR accuracy
        
        Args:
            image: Input image (BGR format)
            
        Returns:
            Preprocessed image
        """
        processed = image.copy()
        
        # Convert to grayscale
        if self.enable_grayscale:
            if len(processed.shape) == 3:
                processed = cv2.cvtColor(processed, cv2.COLOR_BGR2GRAY)
        
        # Denoise
        if self.enable_denoise:
            processed = cv2.fastNlMeansDenoising(processed, None, 10, 7, 21)
        
        # Resize for better OCR (larger is often better)
        scale_factor = 2.0
        processed = cv2.resize(
            processed,
            None,
            fx=scale_factor,
            fy=scale_factor,
            interpolation=cv2.INTER_CUBIC
        )
        
        # Histogram equalization (improve contrast)
        processed = cv2.equalizeHist(processed)
        
        # Adaptive thresholding
        if self.enable_threshold:
            processed = cv2.adaptiveThreshold(
                processed,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                11,
                2
            )
        
        return processed
    
    def extract_text(self, image: np.ndarray) -> Dict[str, Any]:
        """
        Extract text from image using Tesseract OCR
        
        Args:
            image: Input image
            
        Returns:
            Dictionary with OCR results
        """
        result = None

        with self._profile_stage(
            "ocr_extract",
            notes=lambda: {
                "success": bool(result and result.get("success")),
                "confidence": (
                    round(float(result.get("confidence", 0.0)), 3)
                    if result else None
                ),
                "word_count": len(result.get("words", [])) if result else 0,
            },
        ):
            try:
                # Run Tesseract with detailed output
                ocr_data = pytesseract.image_to_data(
                    image,
                    lang=self.ocr_language,
                    config=self.ocr_config,
                    output_type=pytesseract.Output.DICT
                )

                # Extract text and confidence scores
                words = []
                confidences = []

                for i, text in enumerate(ocr_data['text']):
                    if text.strip():  # Non-empty text
                        conf = float(ocr_data['conf'][i])
                        if conf > 0:  # Valid confidence
                            words.append(text)
                            confidences.append(conf / 100.0)  # Normalize to 0-1

                # Combine into full text
                full_text = ' '.join(words)
                avg_confidence = np.mean(confidences) if confidences else 0.0

                result = {
                    'text': full_text,
                    'words': words,
                    'confidence': avg_confidence,
                    'word_confidences': confidences,
                    'success': len(words) > 0
                }
                return result

            except Exception as e:
                self.logger.error(f"OCR extraction failed: {e}")
                result = {
                    'text': '',
                    'words': [],
                    'confidence': 0.0,
                    'word_confidences': [],
                    'success': False,
                    'error': str(e)
                }
                return result
            
    def parse_medicine_name(self, text: str) -> Optional[str]:
        """
        Parse medicine name from OCR text
        
        Args:
            text: Raw OCR text
            
        Returns:
            Cleaned medicine name or None
        """
        if not text:
            return None
        
        # Clean text
        text = text.strip()
        
        # Common patterns for medicine names
        # Example: "Aspirin 100mg" -> extract "Aspirin"
        patterns = [
            r'([A-Za-z]+)\s*\d+\s*mg',  # Name followed by dosage
            r'([A-Za-z]+)\s*\d+\s*g',
            r'^([A-Za-z\s]+?)\s*\d',     # Name before any number
            r'^([A-Za-z\s]+)$'           # Just letters
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                medicine_name = match.group(1).strip()
                if len(medicine_name) >= 3:  # Minimum length
                    return medicine_name
        
        # If no pattern matches, return the longest word
        words = text.split()
        if words:
            longest_word = max(words, key=len)
            if len(longest_word) >= 3:
                return longest_word
        
        return None
        
    def scan_label(self, num_attempts: int = 3, delay_between_attempts: float = 0.5) -> Dict[str, Any]:
        """
        Scan medicine label with multiple attempts
        
        Args:
            num_attempts: Number of scan attempts
            delay_between_attempts: Delay between attempts in seconds
            
        Returns:
            Scan result dictionary
        """
        best_result = None
        best_confidence = 0.0
        result = None

        with self._profile_stage(
            "scan_attempt_total",
            notes=lambda: {
                "num_attempts": num_attempts,
                "best_confidence": round(float(best_confidence), 3),
                "success": bool(result and result.get("success")),
                "medicine_name": result.get("medicine_name") if result else None,
            },
        ):
            if not self.camera_ready:
                if not self.initialize_camera():
                    result = {
                        'success': False,
                        'error': 'Camera initialization failed',
                        'medicine_name': None,
                        'confidence': 0.0
                    }
                    return result

            self.logger.info(f"Starting label scan ({num_attempts} attempts)...")

            for attempt in range(num_attempts):
                self.logger.debug(f"Scan attempt {attempt + 1}/{num_attempts}")

                # Capture frame
                frame = self.capture_frame()
                if frame is None:
                    continue

                # Preprocess
                processed = self.preprocess_image(frame)

                # Extract text
                ocr_result = self.extract_text(processed)

                if ocr_result['success'] and ocr_result['confidence'] > best_confidence:
                    best_confidence = ocr_result['confidence']
                    best_result = ocr_result

                    self.logger.debug(
                        f"Attempt {attempt + 1}: '{ocr_result['text']}' "
                        f"(confidence: {ocr_result['confidence']:.2f})"
                    )

                # If we got high confidence, no need for more attempts
                if best_confidence >= 0.9:
                    self.logger.info("High confidence achieved, stopping early")
                    break

                if attempt < num_attempts - 1:
                    time.sleep(delay_between_attempts)

            # Parse medicine name from best result
            if best_result and best_result['success']:
                medicine_name = self.parse_medicine_name(best_result['text'])

                result = {
                    'success': medicine_name is not None,
                    'medicine_name': medicine_name,
                    'raw_text': best_result['text'],
                    'confidence': best_confidence,
                    'verified': best_confidence >= self.min_confidence
                }

                if result['success']:
                    self.logger.info(
                        f"Scan successful: '{medicine_name}' "
                        f"(confidence: {best_confidence:.2f})"
                    )
                else:
                    self.logger.warning("Could not parse medicine name from OCR text")

                return result

            # All attempts failed
            self.logger.error("All scan attempts failed")
            result = {
                'success': False,
                'error': 'No readable text detected',
                'medicine_name': None,
                'confidence': 0.0,
                'verified': False
            }
            return result
        
    def verify_medicine(self, expected_medicine: str, scanned_medicine: str) -> Dict[str, Any]:
        """
        Verify if scanned medicine matches expected
        
        Args:
            expected_medicine: Expected medicine name
            scanned_medicine: Scanned medicine name
            
        Returns:
            Verification result
        """
        if not scanned_medicine or not expected_medicine:
            return {
                'match': False,
                'reason': 'Missing medicine name'
            }
        
        # Normalize names (lowercase, remove extra spaces)
        expected_norm = expected_medicine.lower().strip()
        scanned_norm = scanned_medicine.lower().strip()
        
        # Exact match
        if expected_norm == scanned_norm:
            return {
                'match': True,
                'match_type': 'exact',
                'confidence': 1.0
            }
        
        # Substring match (e.g., "Aspirin" in "Aspirin 100mg")
        if expected_norm in scanned_norm or scanned_norm in expected_norm:
            return {
                'match': True,
                'match_type': 'substring',
                'confidence': 0.9
            }
        
        # Fuzzy match (simple Levenshtein-like)
        similarity = self._calculate_similarity(expected_norm, scanned_norm)
        
        if similarity >= 0.8:
            return {
                'match': True,
                'match_type': 'fuzzy',
                'confidence': similarity
            }
        
        return {
            'match': False,
            'reason': f"'{scanned_medicine}' does not match '{expected_medicine}'",
            'similarity': similarity
        }
    
    def _calculate_similarity(self, str1: str, str2: str) -> float:
        """
        Calculate similarity between two strings (simple version)
        
        Args:
            str1: First string
            str2: Second string
            
        Returns:
            Similarity score (0.0 to 1.0)
        """
        # Simple character-level similarity
        set1 = set(str1)
        set2 = set(str2)
        
        intersection = len(set1.intersection(set2))
        union = len(set1.union(set2))
        
        if union == 0:
            return 0.0
        
        return intersection / union
    
    def add_known_medicine(self, medicine_name: str):
        """
        Add medicine to known medicines database
        
        Args:
            medicine_name: Medicine name
        """
        if medicine_name and medicine_name not in self.known_medicines:
            self.known_medicines.append(medicine_name)
            self.logger.info(f"Added to known medicines: {medicine_name}")
    
    def get_known_medicines(self) -> List[str]:
        """Get list of known medicines"""
        return self.known_medicines.copy()
