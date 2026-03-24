"""
Smart Medication System - Decision Engine Module

Rule-based decision engine that combines inputs from all sensors and 
modules to make medication verification decisions and generate alerts.
"""

import time
from typing import Dict, Any, Optional, List
from enum import Enum


class DecisionResult(Enum):
    """Decision outcomes"""
    SUCCESS = "success"                    # All checks passed
    INCORRECT_DOSAGE = "incorrect_dosage"  # Wrong number of pills
    WRONG_MEDICINE = "wrong_medicine"      # OCR mismatch
    BEHAVIORAL_ISSUE = "behavioral_issue"  # Coughing/no swallow
    NO_INTAKE = "no_intake"                # No pills taken
    SENSOR_ERROR = "sensor_error"          # Sensor failure
    PARTIAL_SUCCESS = "partial_success"    # Some checks failed but acceptable


class DecisionEngine:
    def __init__(self, config: dict, logger):
        self.config = config
        self.logger = logger

        tolerance_config = config.get("tolerance", {})
        self.pill_count_tolerance = tolerance_config.get("pill_count", 0)
        self.weight_error_g = float(tolerance_config.get("weight_error_g", 0.12))

        verification_config = config.get("verification", {})
        self.require_identity = verification_config.get("require_identity", True)
        self.require_ocr = verification_config.get("require_ocr", False)
        self.require_weight = verification_config.get("require_weight", True)
        self.require_monitoring = verification_config.get("require_monitoring", True)

        self.logger.info("Decision engine initialized")

    def verify_medication_intake(
        self,
        expected_medicine: str,
        expected_dosage: int,
        identity_result: Optional[Dict[str, Any]] = None,
        ocr_result: Optional[Dict[str, Any]] = None,
        weight_result: Optional[Dict[str, Any]] = None,
        monitoring_result: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        decision = {
            "timestamp": time.time(),
            "expected_medicine": expected_medicine,
            "expected_dosage": expected_dosage,
            "result": None,
            "verified": False,
            "alerts": [],
            "details": {},
            "scores": {}
        }

        identity_verified = False
        if identity_result is not None:
            identity_verified = self._verify_identity(identity_result, decision)
        elif self.require_identity:
            decision["alerts"].append({
                "type": "identity_missing",
                "severity": "critical",
                "message": "Identity verification not available"
            })

        ocr_verified = False
        if ocr_result:
            ocr_verified = self._verify_ocr(expected_medicine, ocr_result, decision)

        weight_verified = False
        if weight_result:
            weight_verified = self._verify_weight(expected_dosage, weight_result, decision)
        elif self.require_weight:
            decision["result"] = DecisionResult.SENSOR_ERROR
            decision["alerts"].append({
                "type": "weight_missing",
                "severity": "critical",
                "message": "Weight verification not available"
            })
            return decision

        behavior_verified = False
        if monitoring_result:
            behavior_verified = self._verify_behavior(monitoring_result, decision)

        if self.require_identity and not identity_verified:
            decision["result"] = DecisionResult.WRONG_MEDICINE
            decision["verified"] = False
            return decision

        if not weight_verified:
            actual = decision["details"].get("weight_actual", 0)
            decision["result"] = (
                DecisionResult.NO_INTAKE if actual == 0
                else DecisionResult.INCORRECT_DOSAGE
            )
            decision["verified"] = False
            return decision

        if self.require_monitoring and monitoring_result is not None and not behavior_verified:
            status = decision["details"].get("behavior_status")
            decision["result"] = (
                DecisionResult.NO_INTAKE if status == "no_intake"
                else DecisionResult.BEHAVIORAL_ISSUE
            )
            decision["verified"] = False
            return decision

        if self.require_ocr and not ocr_verified:
            decision["result"] = DecisionResult.WRONG_MEDICINE
            decision["verified"] = False
            return decision

        decision["result"] = DecisionResult.SUCCESS
        decision["verified"] = True
        return decision

    def _verify_identity(self, identity_result: Dict[str, Any], decision: Dict[str, Any]) -> bool:
        success = bool(identity_result.get("success", False))
        decision["details"]["identity_method"] = identity_result.get("method", "none")
        decision["details"]["identity_reason"] = identity_result.get("reason", "")
        decision["details"]["identity_medicine_id"] = identity_result.get("medicine_id")
        decision["details"]["identity_medicine_name"] = identity_result.get("medicine_name")

        if success:
            decision["scores"]["identity"] = float(identity_result.get("confidence", 1.0))
            decision["details"]["identity_verified"] = True
            return True

        decision["scores"]["identity"] = 0.0
        decision["details"]["identity_verified"] = False
        decision["alerts"].append({
            "type": "wrong_medicine",
            "severity": "critical",
            "message": identity_result.get("reason", "Identity verification failed")
        })
        return False

    def _verify_ocr(self, expected_medicine: str, ocr_result: Dict[str, Any], decision: Dict[str, Any]) -> bool:
        if not ocr_result.get("success"):
            decision["scores"]["ocr"] = 0.0
            return False

        scanned_medicine = ocr_result.get("medicine_name", "")
        confidence = float(ocr_result.get("confidence", 0.0))

        match = self._compare_medicine_names(expected_medicine, scanned_medicine)

        decision["details"]["ocr_scanned"] = scanned_medicine
        decision["details"]["ocr_confidence"] = confidence
        decision["details"]["ocr_match"] = match
        decision["scores"]["ocr"] = confidence if match else 0.0

        return match and confidence >= 0.75

    def _verify_weight(self, expected_dosage: int, weight_result: Dict[str, Any], decision: Dict[str, Any]) -> bool:
        actual_dosage = int(weight_result.get("actual", 0) or 0)
        difference = abs(actual_dosage - expected_dosage)

        expected_delta_g = weight_result.get("expected_delta_g")
        actual_delta_g = weight_result.get("weight_change_g")
        delta_error_g = None
        if expected_delta_g is not None and actual_delta_g is not None:
            delta_error_g = abs(float(actual_delta_g) - float(expected_delta_g))

        within_count = difference <= self.pill_count_tolerance
        within_weight = True if delta_error_g is None else delta_error_g <= self.weight_error_g
        ok = within_count and within_weight

        decision["details"]["weight_expected"] = expected_dosage
        decision["details"]["weight_actual"] = actual_dosage
        decision["details"]["weight_difference"] = difference
        decision["details"]["weight_change_g"] = actual_delta_g
        decision["details"]["expected_delta_g"] = expected_delta_g
        decision["details"]["delta_error_g"] = delta_error_g
        decision["scores"]["weight"] = 1.0 if ok else 0.0

        if not ok:
            decision["alerts"].append({
                "type": "incorrect_dosage",
                "severity": "critical",
                "message": f"Incorrect dosage: expected {expected_dosage}, detected {actual_dosage}"
            })

        return ok

    def _verify_behavior(self, monitoring_result: Dict[str, Any], decision: Dict[str, Any]) -> bool:
        status = monitoring_result.get("compliance_status", "unclear")
        decision["details"]["behavior_status"] = status
        decision["details"]["swallow_count"] = monitoring_result.get("swallow_count", 0)
        decision["details"]["cough_count"] = monitoring_result.get("cough_count", 0)
        decision["details"]["hand_motion_count"] = monitoring_result.get("hand_motion_count", 0)

        score_map = {
            "good": 1.0,
            "acceptable": 0.8,
            "concerning": 0.4,
            "no_intake": 0.0,
            "unclear": 0.5,
        }
        decision["scores"]["behavior"] = score_map.get(status, 0.5)
        return status in ["good", "acceptable"]

    def _compare_medicine_names(self, expected: str, scanned: str) -> bool:
        if not expected or not scanned:
            return False
        expected = expected.lower().strip()
        scanned = scanned.lower().strip()
        return expected == scanned or expected in scanned or scanned in expected

    def get_alert_messages(self, decision: Dict[str, Any]) -> Dict[str, str]:
        result = decision["result"]
        medicine = decision.get("expected_medicine", "medication")

        if result == DecisionResult.SUCCESS:
            return {
                "patient_message": f"{medicine} taken successfully.",
                "caregiver_message": f"{medicine} was taken successfully."
            }
        if result == DecisionResult.WRONG_MEDICINE:
            return {
                "patient_message": "Wrong medicine detected. Please use the correct bottle.",
                "caregiver_message": f"Wrong medicine detected for {medicine}."
            }
        if result == DecisionResult.INCORRECT_DOSAGE:
            expected = decision.get("expected_dosage", 0)
            actual = decision.get("details", {}).get("weight_actual", 0)
            return {
                "patient_message": f"Incorrect dosage. Expected {expected}, detected {actual}.",
                "caregiver_message": f"Incorrect dosage for {medicine}. Expected {expected}, detected {actual}."
            }
        if result == DecisionResult.NO_INTAKE:
            return {
                "patient_message": "No intake detected. Please take your medication.",
                "caregiver_message": f"No intake detected for {medicine}."
            }
        return {
            "patient_message": "Verification needs attention.",
            "caregiver_message": f"Verification issue detected for {medicine}."
        }

    def should_alert_caregiver(self, decision: Dict[str, Any]) -> bool:
        return not decision.get("verified", False)
