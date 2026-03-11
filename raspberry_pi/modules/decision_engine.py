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
    """
    Decision engine for medication verification
    
    Combines:
    - OCR results (medicine identification)
    - Weight sensor data (dosage verification)
    - Patient monitoring results (behavioral compliance)
    """
    
    def __init__(self, config: dict, logger):
        """
        Initialize decision engine
        
        Args:
            config: Decision engine configuration
            logger: Logger instance
        """
        self.config = config
        self.logger = logger
        
        # Tolerance settings
        tolerance_config = config.get('tolerance', {})
        self.pill_count_tolerance = tolerance_config.get('pill_count', 1)
        
        # Verification requirements
        verification_config = config.get('verification', {})
        self.require_ocr = verification_config.get('require_ocr', False)
        self.require_weight = verification_config.get('require_weight', True)
        self.require_monitoring = verification_config.get('require_monitoring', True)
        
        # Alert settings
        alert_config = config.get('alerts', {})
        self.alert_missed_dose = alert_config.get('missed_dose', True)
        self.alert_incorrect_dosage = alert_config.get('incorrect_dosage', True)
        self.alert_behavioral_issue = alert_config.get('behavioral_issue', True)
        
        self.logger.info("Decision engine initialized")
    
    def verify_medication_intake(
        self,
        expected_medicine: str,
        expected_dosage: int,
        ocr_result: Optional[Dict[str, Any]] = None,
        weight_result: Optional[Dict[str, Any]] = None,
        monitoring_result: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Verify medication intake based on all available data
        
        Args:
            expected_medicine: Expected medication name
            expected_dosage: Expected number of pills
            ocr_result: OCR verification result (optional)
            weight_result: Weight sensor verification result (optional)
            monitoring_result: Patient monitoring result (optional)
            
        Returns:
            Decision dictionary with outcome, alerts, and details
        """
        self.logger.info(
            f"Verifying medication intake: {expected_medicine} ({expected_dosage} pills)"
        )
        
        # Initialize decision
        decision = {
            'timestamp': time.time(),
            'expected_medicine': expected_medicine,
            'expected_dosage': expected_dosage,
            'result': None,
            'verified': False,
            'alerts': [],
            'details': {},
            'scores': {}
        }
        
        # Verify OCR (if available and required)
        ocr_verified = False
        if ocr_result:
            ocr_verified = self._verify_ocr(
                expected_medicine, 
                ocr_result, 
                decision
            )
        elif self.require_ocr:
            decision['alerts'].append({
                'type': 'ocr_missing',
                'severity': 'warning',
                'message': 'OCR verification not available'
            })
        
        # Verify weight/dosage (if available and required)
        weight_verified = False
        if weight_result:
            weight_verified = self._verify_weight(
                expected_dosage,
                weight_result,
                decision
            )
        elif self.require_weight:
            decision['alerts'].append({
                'type': 'weight_missing',
                'severity': 'error',
                'message': 'Weight verification not available'
            })
            decision['result'] = DecisionResult.SENSOR_ERROR
            return decision
        
        # Verify patient behavior (if available and required)
        behavior_verified = False
        if monitoring_result:
            behavior_verified = self._verify_behavior(
                monitoring_result,
                decision
            )
        elif self.require_monitoring:
            decision['alerts'].append({
                'type': 'monitoring_missing',
                'severity': 'warning',
                'message': 'Patient monitoring not available'
            })
        
        # Make final decision
        self._make_final_decision(
            decision,
            ocr_verified,
            weight_verified,
            behavior_verified
        )
        
        self.logger.info(
            f"Decision: {decision['result'].value} "
            f"(verified: {decision['verified']}, alerts: {len(decision['alerts'])})"
        )
        
        return decision
    
    def _verify_ocr(
        self,
        expected_medicine: str,
        ocr_result: Dict[str, Any],
        decision: Dict[str, Any]
    ) -> bool:
        """
        Verify OCR result against expected medicine
        
        Args:
            expected_medicine: Expected medication name
            ocr_result: OCR scan result
            decision: Decision dictionary to update
            
        Returns:
            True if OCR verified
        """
        if not ocr_result.get('success'):
            decision['details']['ocr_status'] = 'failed'
            decision['scores']['ocr'] = 0.0
            return False
        
        scanned_medicine = ocr_result.get('medicine_name', '')
        confidence = ocr_result.get('confidence', 0.0)
        
        # Check if medicine matches
        medicine_match = self._compare_medicine_names(
            expected_medicine,
            scanned_medicine
        )
        
        decision['details']['ocr_scanned'] = scanned_medicine
        decision['details']['ocr_confidence'] = confidence
        decision['details']['ocr_match'] = medicine_match
        decision['scores']['ocr'] = confidence if medicine_match else 0.0
        
        if not medicine_match:
            decision['alerts'].append({
                'type': 'wrong_medicine',
                'severity': 'critical',
                'message': f"Medicine mismatch: expected '{expected_medicine}', "
                          f"scanned '{scanned_medicine}'"
            })
            return False
        
        if confidence < 0.75:
            decision['alerts'].append({
                'type': 'low_confidence',
                'severity': 'warning',
                'message': f"Low OCR confidence: {confidence:.2f}"
            })
        
        return medicine_match and confidence >= 0.75
    
    def _verify_weight(
        self,
        expected_dosage: int,
        weight_result: Dict[str, Any],
        decision: Dict[str, Any]
    ) -> bool:
        """
        Verify weight/dosage against expected
        
        Args:
            expected_dosage: Expected number of pills
            weight_result: Weight verification result
            decision: Decision dictionary to update
            
        Returns:
            True if weight verified
        """
        if not weight_result.get('verified'):
            decision['details']['weight_status'] = 'not_verified'
            decision['scores']['weight'] = 0.0
            return False
        
        actual_dosage = weight_result.get('actual', 0)
        difference = abs(actual_dosage - expected_dosage)
        within_tolerance = difference <= self.pill_count_tolerance
        
        decision['details']['weight_expected'] = expected_dosage
        decision['details']['weight_actual'] = actual_dosage
        decision['details']['weight_difference'] = difference
        decision['details']['weight_within_tolerance'] = within_tolerance
        
        # Calculate score based on difference
        if difference == 0:
            score = 1.0
        elif difference <= self.pill_count_tolerance:
            score = 0.8
        else:
            score = max(0.0, 1.0 - (difference / (expected_dosage + 1)))
        
        decision['scores']['weight'] = score
        
        if not within_tolerance:
            decision['alerts'].append({
                'type': 'incorrect_dosage',
                'severity': 'critical',
                'message': f"Incorrect dosage: expected {expected_dosage}, "
                          f"detected {actual_dosage} (±{self.pill_count_tolerance} tolerance)"
            })
            return False
        
        return True
    
    def _verify_behavior(
        self,
        monitoring_result: Dict[str, Any],
        decision: Dict[str, Any]
    ) -> bool:
        """
        Verify patient behavior during intake
        
        Args:
            monitoring_result: Patient monitoring result
            decision: Decision dictionary to update
            
        Returns:
            True if behavior acceptable
        """
        compliance_status = monitoring_result.get('compliance_status', 'unclear')
        swallow_count = monitoring_result.get('swallow_count', 0)
        cough_count = monitoring_result.get('cough_count', 0)
        hand_motion_count = monitoring_result.get('hand_motion_count', 0)
        
        decision['details']['behavior_status'] = compliance_status
        decision['details']['swallow_count'] = swallow_count
        decision['details']['cough_count'] = cough_count
        decision['details']['hand_motion_count'] = hand_motion_count
        
        # Score based on compliance status
        status_scores = {
            'good': 1.0,
            'acceptable': 0.8,
            'concerning': 0.4,
            'no_intake': 0.0,
            'unclear': 0.5
        }
        
        score = status_scores.get(compliance_status, 0.5)
        decision['scores']['behavior'] = score
        
        # Generate alerts based on status
        if compliance_status == 'concerning':
            decision['alerts'].append({
                'type': 'behavioral_issue',
                'severity': 'warning',
                'message': f"Concerning behavior detected: {cough_count} coughs"
            })
            return False
        
        elif compliance_status == 'no_intake':
            decision['alerts'].append({
                'type': 'no_intake_detected',
                'severity': 'critical',
                'message': "No swallowing or intake motion detected"
            })
            return False
        
        elif compliance_status == 'unclear':
            decision['alerts'].append({
                'type': 'unclear_behavior',
                'severity': 'warning',
                'message': "Unable to clearly verify intake behavior"
            })
            return False
        
        return compliance_status in ['good', 'acceptable']
    
    def _compare_medicine_names(self, expected: str, scanned: str) -> bool:
        """
        Compare medicine names with fuzzy matching
        
        Args:
            expected: Expected medicine name
            scanned: Scanned medicine name
            
        Returns:
            True if match
        """
        if not expected or not scanned:
            return False
        
        # Normalize
        expected_norm = expected.lower().strip()
        scanned_norm = scanned.lower().strip()
        
        # Exact match
        if expected_norm == scanned_norm:
            return True
        
        # Substring match
        if expected_norm in scanned_norm or scanned_norm in expected_norm:
            return True
        
        # Character-level similarity
        set1 = set(expected_norm)
        set2 = set(scanned_norm)
        intersection = len(set1.intersection(set2))
        union = len(set1.union(set2))
        
        if union > 0:
            similarity = intersection / union
            return similarity >= 0.7
        
        return False
    
    def _make_final_decision(
        self,
        decision: Dict[str, Any],
        ocr_verified: bool,
        weight_verified: bool,
        behavior_verified: bool
    ):
        """
        Make final verification decision
        
        Args:
            decision: Decision dictionary to update
            ocr_verified: OCR verification result
            weight_verified: Weight verification result
            behavior_verified: Behavior verification result
        """
        # Calculate overall score
        scores = decision['scores']
        
        # Weighted average (weight is most important)
        weights = {'ocr': 0.2, 'weight': 0.5, 'behavior': 0.3}
        
        overall_score = 0.0
        total_weight = 0.0
        
        for key, weight in weights.items():
            if key in scores:
                overall_score += scores[key] * weight
                total_weight += weight
        
        if total_weight > 0:
            overall_score /= total_weight
        
        decision['scores']['overall'] = overall_score
        
        # Determine result
        critical_alerts = [a for a in decision['alerts'] if a['severity'] == 'critical']
        
        if not weight_verified:
            # Weight is mandatory - check specific failure
            if decision['details'].get('weight_actual', 0) == 0:
                decision['result'] = DecisionResult.NO_INTAKE
            else:
                decision['result'] = DecisionResult.INCORRECT_DOSAGE
            decision['verified'] = False
        
        elif not behavior_verified and self.require_monitoring:
            decision['result'] = DecisionResult.BEHAVIORAL_ISSUE
            decision['verified'] = False
        
        elif not ocr_verified and self.require_ocr:
            decision['result'] = DecisionResult.WRONG_MEDICINE
            decision['verified'] = False
        
        elif len(critical_alerts) > 0:
            decision['result'] = DecisionResult.PARTIAL_SUCCESS
            decision['verified'] = False
        
        elif overall_score >= 0.8:
            decision['result'] = DecisionResult.SUCCESS
            decision['verified'] = True
        
        else:
            decision['result'] = DecisionResult.PARTIAL_SUCCESS
            decision['verified'] = overall_score >= 0.6
    
    def should_alert_caregiver(self, decision: Dict[str, Any]) -> bool:
        """
        Determine if caregiver should be alerted
        
        Args:
            decision: Decision result
            
        Returns:
            True if caregiver alert needed
        """
        result = decision['result']
        
        # Always alert on critical failures
        if result in [
            DecisionResult.INCORRECT_DOSAGE,
            DecisionResult.WRONG_MEDICINE,
            DecisionResult.NO_INTAKE
        ]:
            return True
        
        # Alert on behavioral issues if configured
        if result == DecisionResult.BEHAVIORAL_ISSUE and self.alert_behavioral_issue:
            return True
        
        # Alert if there are critical alerts
        critical_alerts = [
            a for a in decision['alerts'] 
            if a['severity'] == 'critical'
        ]
        
        return len(critical_alerts) > 0
        
    def get_alert_messages(self, decision: Dict[str, Any]) -> Dict[str, str]:
        """
        Generate alert messages for different channels
        
        Args:
            decision: Decision result
            
        Returns:
            Dictionary with patient_message and caregiver_message
        """
        result = decision['result']
        
        # Patient message (keep it simple and non-alarming)
        patient_messages = {
            DecisionResult.SUCCESS: "Medication taken successfully!",
            DecisionResult.INCORRECT_DOSAGE: "Please verify your dosage.",
            DecisionResult.WRONG_MEDICINE: "Please check the medication label.",
            DecisionResult.BEHAVIORAL_ISSUE: "Please ensure you swallow the medication.",
            DecisionResult.NO_INTAKE: "No medication intake detected.",
            DecisionResult.PARTIAL_SUCCESS: "Medication recorded, please verify.",
            DecisionResult.SENSOR_ERROR: "Sensor error, please contact support."
        }
        
        # Caregiver message (detailed)
        caregiver_message = f"Medication Verification Alert\n\n"
        caregiver_message += f"Medicine: {decision['expected_medicine']}\n"
        caregiver_message += f"Expected: {decision['expected_dosage']} pills\n"
        caregiver_message += f"Result: {result.value}\n"
        caregiver_message += f"Verified: {'Yes' if decision['verified'] else 'No'}\n\n"
        
        if decision['alerts']:
            caregiver_message += "Alerts:\n"
            for alert in decision['alerts']:
                caregiver_message += f"- {alert['message']}\n"
        
        return {
            'patient_message': patient_messages.get(
                result, 
                "Please contact your caregiver."
            ),
            'caregiver_message': caregiver_message
        }
