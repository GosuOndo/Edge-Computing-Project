from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.qr_scanner import QRScanner


def main():
    print("Starting QR verify test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    scanner = QRScanner(logger)

    sample_qr = {
        "medicine_id": "MED001",
        "patient_id": "P001",
        "medicine_name": "AMLODIPINE",
        "strength": "5MG",
        "dosage_amount": 1,
        "dosage_unit": "TABLET",
        "time_slot": "MORNING",
        "meal_rule": "AFTER_MEAL"
    }

    result_match = scanner.verify_medicine(sample_qr, "AMLODIPINE")
    result_mismatch = scanner.verify_medicine(sample_qr, "PANADOL")

    print("\nMatch result:")
    print(result_match)

    print("\nMismatch result:")
    print(result_mismatch)


if __name__ == "__main__":
    main()
