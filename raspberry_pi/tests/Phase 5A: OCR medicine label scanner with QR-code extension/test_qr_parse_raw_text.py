from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.qr_scanner import QRScanner


def main():
    print("Starting raw QR text parse test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    scanner = QRScanner(logger)

    raw_json = """{
      "medicine_id": "MED001",
      "patient_id": "P001",
      "medicine_name": "AMLODIPINE",
      "strength": "5MG",
      "dosage_amount": 1,
      "dosage_unit": "TABLET",
      "frequency": "ONCE_DAILY",
      "time_slot": "MORNING",
      "meal_rule": "AFTER_MEAL",
      "route": "ORAL",
      "notes": "TAKE_WITH_WATER"
    }"""

    parsed = scanner.parse_qr_text(raw_json)

    print("\nParsed dictionary:")
    print(parsed)

    validation = scanner.validate_required_fields(parsed)

    print("\nValidation result:")
    print(validation)


if __name__ == "__main__":
    main()
