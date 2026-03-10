"""
Smart Medication System - QR Scanner Module

Decodes QR codes from medication labels and parses structured QR data.

Supported formats:
1. JSON text
2. key=value lines
"""

import json
from typing import Dict, Any, Optional, List
from pyzbar.pyzbar import decode


class QRScanner:
    """QR-based medication label scanner."""

    REQUIRED_FIELDS = [
        "medicine_id",
        "patient_id",
        "medicine_name",
        "strength",
        "dosage_amount",
        "dosage_unit",
        "time_slot",
        "meal_rule"
    ]

    def __init__(self, logger):
        self.logger = logger
        self.logger.info("QR scanner initialised")

    def decode_image(self, image) -> List[Dict[str, Any]]:
        """
        Decode all QR codes in an image.
        """
        try:
            results = decode(image)
            decoded_results = []

            for result in results:
                try:
                    text = result.data.decode("utf-8")
                except Exception:
                    text = result.data.decode("utf-8", errors="replace")

                decoded_results.append({
                    "type": result.type,
                    "data": text,
                    "rect": {
                        "x": result.rect.left,
                        "y": result.rect.top,
                        "w": result.rect.width,
                        "h": result.rect.height
                    }
                })

            self.logger.info(f"QR decode completed: {len(decoded_results)} code(s) found")
            return decoded_results

        except Exception as e:
            self.logger.error(f"QR decode failed: {e}")
            return []

    def parse_qr_text(self, qr_text: str) -> Dict[str, Any]:
        """
        Parse QR text into a dictionary.
        Supports JSON or key=value lines.
        """
        qr_text = qr_text.strip()

        if not qr_text:
            return {}

        # Try JSON first
        try:
            parsed_json = json.loads(qr_text)
            if isinstance(parsed_json, dict):
                self.logger.info("QR parsed as JSON")
                return parsed_json
        except Exception:
            pass

        # Fallback to key=value
        parsed = {}

        for line in qr_text.splitlines():
            line = line.strip()

            if not line:
                continue

            if "=" in line:
                key, value = line.split("=", 1)
                parsed[key.strip()] = value.strip()

        if parsed:
            self.logger.info("QR parsed as key=value lines")
        else:
            self.logger.warning("QR text could not be parsed")

        return parsed
        
    def decode_and_parse(self, image) -> Optional[Dict[str, Any]]:
        """
        Decode first QR code and parse its contents.
        """
        decoded_results = self.decode_image(image)

        if not decoded_results:
            self.logger.warning("No QR detected")
            return None

        first = decoded_results[0]

        parsed = self.parse_qr_text(first["data"])

        return {
            "raw_text": first["data"],
            "parsed": parsed,
            "type": first["type"],
            "rect": first["rect"],
            "success": True
        }

    def validate_required_fields(self, parsed_qr: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check required medication fields exist.
        """
        missing_fields = [
            field for field in self.REQUIRED_FIELDS
            if field not in parsed_qr
        ]

        return {
            "valid": len(missing_fields) == 0,
            "missing_fields": missing_fields,
            "present_fields": list(parsed_qr.keys())
        }

    def verify_medicine(self, parsed_qr: Dict[str, Any], expected_medicine: str) -> Dict[str, Any]:
        """
        Verify medicine name.
        """
        actual = parsed_qr.get("medicine_name")

        if not actual:
            return {
                "match": False,
                "reason": "medicine_name missing"
            }

        expected_norm = expected_medicine.strip().lower()
        actual_norm = str(actual).strip().lower()

        if expected_norm == actual_norm:
            return {
                "match": True,
                "match_type": "exact",
                "expected": expected_medicine,
                "actual": actual
            }

        return {
            "match": False,
            "reason": f"QR medicine '{actual}' does not match expected '{expected_medicine}'",
            "expected": expected_medicine,
            "actual": actual
        }
