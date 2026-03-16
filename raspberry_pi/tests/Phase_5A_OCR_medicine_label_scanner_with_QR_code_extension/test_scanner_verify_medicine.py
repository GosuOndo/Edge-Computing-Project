from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.medicine_scanner import MedicineScanner

def main():
    print("Starting medicine verification test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    ocr_config = config["ocr"]

    scanner = MedicineScanner(ocr_config, logger)

    tests = [
        ("Panadol", "Panadol"),
        ("Panadol", "Panadol 500mg"),
        ("Aspirin", "Asprin"),
        ("Paracetamol", "Ibuprofen"),
    ]

    for expected, scanned in tests:
        result = scanner.verify_medicine(expected, scanned)
        print(f"Expected: {expected}, Scanned: {scanned}")
        print(result)
        print("-" * 40)

if __name__ == "__main__":
    main()
