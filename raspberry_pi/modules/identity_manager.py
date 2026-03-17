"""
Smart Medication System - Identity Manager

Primary identity verification flow:
1. Tag
2. QR
3. OCR
"""

from typing import Dict, Any, Optional

from raspberry_pi.modules.qr_scanner import QRScanner


class IdentityManager:
    """Handles tag-first, QR-second, OCR-third identity verification."""

    def __init__(self, config: dict, scanner, database, tag_runtime_service, logger):
        self.config = config
        self.scanner = scanner
        self.database = database
        self.tag_runtime_service = tag_runtime_service
        self.logger = logger

        self.qr_scanner = QRScanner(logger)

    def verify_identity(
        self,
        expected_medicine_id: str,
        expected_medicine_name: str,
        expected_station_id: str
    ) -> Dict[str, Any]:
        """
        Run identity verification in priority order:
        tag -> qr -> ocr
        """
        identity_cfg = self.config.get("identity", {})

        # 1. TAG FIRST
        tag_cfg = identity_cfg.get("tag", {})
        if tag_cfg.get("enabled", True):
            tag_result = self.tag_runtime_service.wait_for_matching_tag(
                expected_medicine_id=expected_medicine_id,
                expected_station_id=expected_station_id,
                max_attempts=tag_cfg.get("max_attempts", 3),
                attempt_timeout_seconds=tag_cfg.get("attempt_timeout_seconds", 6)
            )

            if tag_result.get("success"):
                record = tag_result["record"]
                return {
                    "success": True,
                    "method": "tag",
                    "medicine_id": record.get("medicine_id"),
                    "medicine_name": record.get("medicine_name"),
                    "station_id": record.get("station_id"),
                    "verified": True,
                    "confidence": 1.0,
                    "raw_result": tag_result
                }

            self.logger.warning(f"Tag verification failed: {tag_result.get('reason')}")

        # 2. QR SECOND
        qr_cfg = identity_cfg.get("qr", {})
        if qr_cfg.get("enabled", True):
            qr_result = self._try_qr(
                expected_medicine_id=expected_medicine_id,
                expected_medicine_name=expected_medicine_name,
                expected_station_id=expected_station_id,
                max_attempts=qr_cfg.get("max_attempts", 2)
            )
            if qr_result.get("success"):
                return qr_result

        # 3. OCR LAST
        ocr_cfg = identity_cfg.get("ocr", {})
        if ocr_cfg.get("enabled", True):
            ocr_result = self._try_ocr(
                expected_medicine_name=expected_medicine_name,
                max_attempts=ocr_cfg.get("max_attempts", 2)
            )
            if ocr_result.get("success"):
                return ocr_result

        return {
            "success": False,
            "method": "none",
            "verified": False,
            "reason": "All identity verification methods failed"
        }

    def _try_qr(
        self,
        expected_medicine_id: str,
        expected_medicine_name: str,
        expected_station_id: str,
        max_attempts: int = 2
    ) -> Dict[str, Any]:
        import time
        import cv2

        self.logger.info(f"Trying QR fallback ({max_attempts} attempts)")

        if not self.scanner.camera_ready:
            ok = self.scanner.initialize_camera()
            if not ok:
                return {
                    "success": False,
                    "method": "qr",
                    "reason": "Camera initialization failed for QR"
                }

        # Let webcam settle after opening
        time.sleep(2.0)

        # Warm-up reads exactly like the working test
        last_frame = None
        for i in range(20):
            frame = self.scanner.capture_frame()
            if frame is not None:
                last_frame = frame
            self.logger.debug(
                f"QR warm-up frame {i+1}/20: {'OK' if frame is not None else 'FAILED'}"
            )
            time.sleep(0.1)

        # Optional debug frame save
        if last_frame is not None:
            try:
                cv2.imwrite("data/qr_fallback_debug_frame.jpg", last_frame)
                self.logger.info("Saved QR fallback debug frame to data/qr_fallback_debug_frame.jpg")
            except Exception as e:
                self.logger.warning(f"Could not save QR fallback debug frame: {e}")

        for attempt in range(1, max_attempts + 1):
            self.logger.info(f"QR attempt {attempt}/{max_attempts}")

            for frame_idx in range(15):
                frame = self.scanner.capture_frame()
                if frame is None:
                    time.sleep(0.1)
                    continue

                qr_scan = self.qr_scanner.decode_and_parse(frame)
                if not qr_scan:
                    time.sleep(0.2)
                    continue

                parsed = qr_scan.get("parsed", {})
                medicine_id = parsed.get("medicine_id")
                medicine_name = parsed.get("medicine_name")
                station_id = parsed.get("station_id", expected_station_id)

                if medicine_id and medicine_id == expected_medicine_id:
                    self.logger.info(
                        f"QR verification succeeded on attempt {attempt}, frame {frame_idx + 1}"
                    )
                    return {
                        "success": True,
                        "method": "qr",
                        "medicine_id": medicine_id,
                        "medicine_name": medicine_name,
                        "station_id": station_id,
                        "verified": True,
                        "confidence": 1.0,
                        "raw_result": qr_scan
                    }

                if medicine_name:
                    qr_verify = self.qr_scanner.verify_medicine(parsed, expected_medicine_name)
                    if qr_verify.get("match"):
                        self.logger.info(
                            f"QR verification succeeded by name on attempt {attempt}, frame {frame_idx + 1}"
                        )
                        return {
                            "success": True,
                            "method": "qr",
                            "medicine_id": medicine_id,
                            "medicine_name": medicine_name,
                            "station_id": station_id,
                            "verified": True,
                            "confidence": 0.95,
                            "raw_result": qr_scan
                        }

            time.sleep(0.5)

        self.logger.warning("QR fallback failed")
        return {
            "success": False,
            "method": "qr",
            "reason": "QR fallback failed"
        }

    def _try_ocr(
        self,
        expected_medicine_name: str,
        max_attempts: int = 2
    ) -> Dict[str, Any]:
        """
        OCR final fallback.
        """
        self.logger.info(f"Trying OCR fallback ({max_attempts} attempts)")

        ocr_result = self.scanner.scan_label(num_attempts=max_attempts)

        if not ocr_result.get("success"):
            return {
                "success": False,
                "method": "ocr",
                "reason": ocr_result.get("error", "OCR failed"),
                "raw_result": ocr_result
            }

        verify = self.scanner.verify_medicine(
            expected_medicine=expected_medicine_name,
            scanned_medicine=ocr_result.get("medicine_name")
        )

        if verify.get("match"):
            return {
                "success": True,
                "method": "ocr",
                "medicine_id": None,
                "medicine_name": ocr_result.get("medicine_name"),
                "station_id": None,
                "verified": True,
                "confidence": ocr_result.get("confidence", 0.0),
                "raw_result": ocr_result
            }

        return {
            "success": False,
            "method": "ocr",
            "reason": verify.get("reason", "OCR medicine mismatch"),
            "raw_result": ocr_result
        }
