"""
Smart Medication System - Tag Manager

Parses compact RFID/NFC tag payloads and verifies them against expected runtime context.
"""

from typing import Dict, Any, Optional, List


class TagManager:
    """Utility class for parsing and validating compact medicine tag payloads."""

    MEAL_RULE_MAP = {
        "AF": "AFTER_MEAL",
        "BF": "BEFORE_MEAL",
        "NM": "NO_MEAL_RULE",
    }

    def __init__(self, logger):
        self.logger = logger

    def parse_payload(self, payload_raw: str) -> Dict[str, Any]:
        """
        Parse compact payload like:
        ID=M001;P=P001;N=ASPIRIN100;D=2;T=08,20;M=AF;S=1;W=290

        W (optional): per-pill weight in milligrams, stored on the tag so the
        system does not rely on hard-coded config values.
        """
        result: Dict[str, Any] = {}

        if not payload_raw:
            return result

        parts = payload_raw.split(";")
        for part in parts:
            if "=" in part:
                key, value = part.split("=", 1)
                result[key.strip()] = value.strip()

        return result

    def _normalise_station_id(self, station_value: Optional[str]) -> Optional[str]:
        if not station_value:
            return None

        station_value = station_value.strip()

        if station_value.startswith("station_"):
            return station_value

        if station_value.isdigit():
            return f"station_{station_value}"

        return station_value

    def _normalise_time_slots(self, time_value: Optional[str]) -> str:
        """
        Convert compact time format like '08,20' into '08:00,20:00'
        """
        if not time_value:
            return ""

        slots: List[str] = []

        for raw in time_value.split(","):
            raw = raw.strip()
            if not raw:
                continue

            if ":" in raw:
                slots.append(raw)
            elif raw.isdigit() and len(raw) <= 2:
                hour = int(raw)
                slots.append(f"{hour:02d}:00")
            else:
                slots.append(raw)

        return ",".join(slots)

    def _normalise_meal_rule(self, meal_value: Optional[str]) -> str:
        if not meal_value:
            return ""

        meal_value = meal_value.strip().upper()
        return self.MEAL_RULE_MAP.get(meal_value, meal_value)

    def build_record_from_scan(self, scan_msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Build a clean database-ready medicine record from a tag scan MQTT message.
        """
        tag_uid = scan_msg.get("tag_uid")
        payload_raw = scan_msg.get("payload_raw", "")

        parsed = self.parse_payload(payload_raw)
        if not parsed:
            self.logger.warning("Tag payload could not be parsed.")
            return None
        medicine_id = parsed.get("ID")
        if not medicine_id:
            self.logger.warning("Tag payload missing medicine ID.")
            return None

        dosage_amount = None
        raw_dose = parsed.get("D")
        if raw_dose:
            try:
                dosage_amount = int(raw_dose)
            except ValueError:
                dosage_amount = None

        pill_weight_mg = None
        raw_weight = parsed.get("W")
        if raw_weight:
            try:
                pill_weight_mg = int(raw_weight)
            except ValueError:
                pill_weight_mg = None

        record = {
            "medicine_id": medicine_id,
            "patient_id": parsed.get("P"),
            "medicine_name": parsed.get("N"),
            "dosage_amount": dosage_amount,
            "dosage_unit": "TABLET",
            "time_slots": self._normalise_time_slots(parsed.get("T")),
            "meal_rule": self._normalise_meal_rule(parsed.get("M")),
            "station_id": self._normalise_station_id(parsed.get("S")),
            "pill_weight_mg": pill_weight_mg,
            "tag_uid": tag_uid,
            "tag_payload": payload_raw,
            "source_method": "tag",
            "active": True,
        }

        return record

    def verify_scan_against_expected(
        self,
        record: Dict[str, Any],
        expected_medicine_id: Optional[str] = None,
        expected_station_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Verify scanned tag record against expected medicine/station context.
        """
        if not record:
            return {
                "match": False,
                "reason": "No record available for verification"
            }

        actual_medicine_id = record.get("medicine_id")
        actual_station_id = record.get("station_id")

        if expected_medicine_id and actual_medicine_id != expected_medicine_id:
            return {
                "match": False,
                "reason": f"Medicine mismatch: expected {expected_medicine_id}, got {actual_medicine_id}",
                "expected_medicine_id": expected_medicine_id,
                "actual_medicine_id": actual_medicine_id,
                "expected_station_id": expected_station_id,
                "actual_station_id": actual_station_id,
            }

        if expected_station_id and actual_station_id != expected_station_id:
            return {
                "match": False,
                "reason": f"Station mismatch: expected {expected_station_id}, got {actual_station_id}",
                "expected_medicine_id": expected_medicine_id,
                "actual_medicine_id": actual_medicine_id,
                "expected_station_id": expected_station_id,
                "actual_station_id": actual_station_id,
            }

        return {
            "match": True,
            "reason": "Tag matches expected runtime context",
            "expected_medicine_id": expected_medicine_id,
            "actual_medicine_id": actual_medicine_id,
            "expected_station_id": expected_station_id,
            "actual_station_id": actual_station_id,
        }
