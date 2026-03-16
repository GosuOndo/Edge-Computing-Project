from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.medicine_scanner import MedicineScanner

def main():
    print("Starting live scan_label test...")
    print("Please place a medicine label clearly in front of the camera.")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    ocr_config = config["ocr"]

    scanner = MedicineScanner(ocr_config, logger)

    result = scanner.scan_label(num_attempts=3, delay_between_attempts=1.0)

    print("scan_label result:")
    print(result)

    scanner.release_camera()
    print("Live scan_label test completed.")

if __name__ == "__main__":
    main()
