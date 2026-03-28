"""
Smart Medication System - Identity Manager

Provides two verification modes (tag-only, no QR/OCR fallback):

1. INTEGRATED (tag reader under scale, default for this project):
   verify_identity_integrated(...)
   Checks for a coincident tag scan near the weight event timestamp.
   Retries up to max_attempts (default 3) with short waits before
   reporting tag not found.

2. LEGACY (separate tag reader, active-wait):
   verify_identity(...)
   Blocks waiting for a matching tag scan. Retries up to max_attempts
   (default 3) before reporting tag not found.

In both modes a hard mismatch (wrong tag scanned) fails immediately with
no further attempts.
"""

from typing import Dict, Any
import time


class IdentityManager:
    """Handles tag-only identity verification (integrated or active-wait mode)."""

    def __init__(self, config: dict, scanner, database, tag_runtime_service, logger):
        self.config = config
        self.scanner = scanner
        self.database = database
        self.tag_runtime_service = tag_runtime_service
        self.logger = logger

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
        1. If a coincident tag is found and it MATCHES   -> success.
        2. If a coincident tag is found and it MISMATCHES -> hard fail, no more attempts.
        3. If no coincident tag is found yet, retry up to max_attempts times
           (1-second wait between retries) before reporting tag not found.
        """
        identity_cfg = self.config.get("identity", {})
        tag_cfg      = identity_cfg.get("tag", {})
        max_attempts = tag_cfg.get("max_attempts", 3)

        if not tag_cfg.get("enabled", True) or weight_event_timestamp is None:
            self.logger.warning("Tag verification disabled or no weight timestamp")
            return {
                "success":  False,
                "method":   "tag_integrated",
                "verified": False,
                "reason":   "Tag verification disabled",
            }

        window = tag_cfg.get("coincident_window_seconds", coincident_window_seconds)

        for attempt in range(1, max_attempts + 1):
            self.logger.info(
                f"Integrated tag check attempt {attempt}/{max_attempts}"
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
                    f"Integrated tag identity verified (attempt {attempt}/{max_attempts}): "
                    f"{record.get('medicine_id')} / {record.get('medicine_name')}"
                )
                return {
                    "success":      True,
                    "method":       "tag_integrated",
                    "medicine_id":  record.get("medicine_id"),
                    "medicine_name": record.get("medicine_name"),
                    "station_id":   record.get("station_id"),
                    "verified":     True,
                    "confidence":   1.0,
                    "raw_result":   tag_result,
                }

            reason = str(tag_result.get("reason", "") or "").lower()

            # Hard fail on actual mismatch - do not retry
            if "mismatch" in reason:
                self.logger.warning(
                    f"Integrated tag mismatch - hard failing (attempt {attempt}): "
                    f"{tag_result.get('reason')}"
                )
                return {
                    "success":   False,
                    "method":    "tag_integrated",
                    "verified":  False,
                    "confidence": 0.0,
                    "reason":    tag_result.get("reason", "Integrated tag mismatch"),
                    "hard_fail": True,
                    "raw_result": tag_result,
                }

            # No tag scan available yet
            if attempt < max_attempts:
                self.logger.info(
                    f"No coincident tag found (attempt {attempt}/{max_attempts}): "
                    f"{tag_result.get('reason')} - retrying in 1s"
                )
                time.sleep(1.0)

        self.logger.warning(
            f"Tag not found after {max_attempts} attempts"
        )
        return {
            "success":  False,
            "method":   "tag_integrated",
            "verified": False,
            "reason":   f"Tag not found after {max_attempts} attempts",
        }

    # ------------------------------------------------------------------
    # Legacy active verification (separate tag reader)
    # ------------------------------------------------------------------

    def verify_identity(
        self,
        expected_medicine_id: str,
        expected_medicine_name: str,
        expected_station_id: str
    ) -> Dict[str, Any]:
        """
        Active-wait tag verification.

        Blocks until a matching tag scan is received or max_attempts
        (default 3) are exhausted. Reports tag not found on failure.
        """
        identity_cfg = self.config.get("identity", {})
        tag_cfg      = identity_cfg.get("tag", {})
        max_attempts = tag_cfg.get("max_attempts", 3)

        if tag_cfg.get("enabled", True):
            tag_result = self.tag_runtime_service.wait_for_matching_tag(
                expected_medicine_id=expected_medicine_id,
                expected_station_id=expected_station_id,
                max_attempts=max_attempts,
                attempt_timeout_seconds=tag_cfg.get("attempt_timeout_seconds", 6)
            )

            if tag_result.get("success"):
                record = tag_result["record"]
                return {
                    "success":      True,
                    "method":       "tag",
                    "medicine_id":  record.get("medicine_id"),
                    "medicine_name": record.get("medicine_name"),
                    "station_id":   record.get("station_id"),
                    "verified":     True,
                    "confidence":   1.0,
                    "raw_result":   tag_result,
                }

            self.logger.warning(
                f"Tag not found after {max_attempts} attempts: "
                f"{tag_result.get('reason')}"
            )

        return {
            "success":  False,
            "method":   "tag",
            "verified": False,
            "reason":   f"Tag not found after {max_attempts} attempts",
        }
