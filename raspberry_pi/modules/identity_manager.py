"""
Smart Medication System - Identity Manager

Provides two verification modes:

1. INTEGRATED (tag reader under scale default for this project):
   verify_identity_integrated(...)
   Checks for a coincident tag scan near the weight event timestamp.
   The bottle tag is read passively when the bottle is placed back on the
   station no patient action required. Falls back to QR then OCR
   if no coincident tag is found within the window.

2. LEGACY (separate tag reader):
   verify_identity(...)
   Original active wait: tag -> QR -> OCR in priority order.
   Kept for backwards compatibility and for the QR/OCR fallback path.
"""

from typing import Dict, Any, Optional
import time

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
        
    # ------------------------------------------------------------------
    # Integrated verification (tag under scale - primary path)
    # ------------------------------------------------------------------

    def verify_identity_integrated(
        self,
        expected_medicine_id: str,
        expected_medicine_name: str,
        expected_station_id: str,
        weight_event_timestamp: float,
        coincident_window_seconds: float = 15.0
    ) -> Dict[str, Any]:
        """
        Integrated identity verification for under-scale RFID workflow.

        Rules:
        1. If a coincident tag is found and it MATCHES -> success.
        2. If a coincident tag is found and it MISMATCHES -> hard fail, no fallback.
        3. If no coincident tag is found -> fallback to active tag/QR/OCR.
        """
        identity_cfg = self.config.get("identity", {})
        tag_cfg = identity_cfg.get("tag", {})

        if tag_cfg.get("enabled", True) and weight_event_timestamp is not None:
            window = tag_cfg.get(
                "coincident_window_seconds", coincident_window_seconds
            )

            tag_result = self.tag_runtime_service.verify_coincident_tag(
                weight_event_timestamp=weight_event_timestamp,
                expected_medicine_id=expected_medicine_id,
                expected_station_id=expected_station_id,
                window_seconds=window
            )

            if tag_result.get("success"):
                record = tag_result["record"]
                self.logger.info(
                    f"Integrated tag identity verified: "
                    f"{record.get('medicine_id')} / {record.get('medicine_name')}"
                )
                return {
                    "success": True,
                    "method": "tag_integrated",
                    "medicine_id": record.get("medicine_id"),
                    "medicine_name": record.get("medicine_name"),
                    "station_id": record.get("station_id"),
                    "verified": True,
                    "confidence": 1.0,
                    "raw_result": tag_result
                }

            reason = str(tag_result.get("reason", "") or "").lower()

            # HARD FAIL on actual mismatch
            if "mismatch" in reason:
                self.logger.warning(
                    f"Integrated tag mismatch detected. Hard failing without fallback: "
                    f"{tag_result.get('reason')}"
                )
                return {
                    "success": False,
                    "method": "tag_integrated",
                    "verified": False,
                    "confidence": 0.0,
                    "reason": tag_result.get("reason", "Integrated tag mismatch"),
                    "hard_fail": True,
                    "raw_result": tag_result
                }

            # Only fall back when there was simply no usable coincident tag
            self.logger.info(
                f"No valid coincident tag match ({tag_result.get('reason')}); "
                f"falling back to QR/OCR"
            )

        return self.verify_identity(
            expected_medicine_id=expected_medicine_id,
            expected_medicine_name=expected_medicine_name,
            expected_station_id=expected_station_id
        )

    # ------------------------------------------------------------------
    # Legacy active verification (original pipeline)
    # ------------------------------------------------------------------

    def verify_identity(
        self,
        expected_medicine_id: str,
        expected_medicine_name: str,
        expected_station_id: str
    ) -> Dict[str, Any]:
        """
        Run identity verification in priority order: tag -> QR -> OCR.

        This is the original active-wait path and is used as a fallback
        from verify_identity_integrated, or directly in non-integrated setups.
        """
        identity_cfg = self.config.get("identity", {})

        # 1. TAG
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

        # 2. QR
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
                
        # 3. OCR
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

    # ------------------------------------------------------------------
    # QR fallback
    # ------------------------------------------------------------------

    def _try_qr(
        self,
        expected_medicine_id: str,
        expected_medicine_name: str,
        expected_station_id: str,
        max_attempts: int = 2
    ) -> Dict[str, Any]:
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

        time.sleep(2.0)

        last_frame = None
        for i in range(20):
            frame = self.scanner.capture_frame()
            if frame is not None:
                last_frame = frame
            time.sleep(0.1)

        if last_frame is not None:
            try:
                cv2.imwrite("data/qr_fallback_debug_frame.jpg", last_frame)
                self.logger.info(
                    "Saved QR fallback debug frame to data/qr_fallback_debug_frame.jpg"
                )
            except Exception as e:
                self.logger.warning(f"Could not save QR debug frame: {e}")

        for attempt in range(1, max_attempts + 1):
            self.logger.info(f"QR attempt {attempt}/{max_attempts}")

            for _ in range(15):
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
                    self.logger.info(f"QR verification succeeded on attempt {attempt}")
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
                    qr_verify = self.qr_scanner.verify_medicine(
                        parsed, expected_medicine_name
                    )
                    if qr_verify.get("match"):
                        self.logger.info(
                            f"QR verification succeeded by name on attempt {attempt}"
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

    # ------------------------------------------------------------------
    # OCR fallback
    # ------------------------------------------------------------------

    def _try_ocr(
        self,
        expected_medicine_name: str,
        max_attempts: int = 2
    ) -> Dict[str, Any]:
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
