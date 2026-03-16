from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.medicine_scanner import MedicineScanner

def main():
    print("Starting scanner init test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    ocr_config = config["ocr"]

    scanner = MedicineScanner(ocr_config, logger)
    success = scanner.initialize_camera()

    print("Scanner created successfully.")
    print("Camera initialized:", success)
    print("Camera ready:", scanner.camera_ready)

    scanner.release_camera()
    print("Scanner init test completed.")

if __name__ == "__main__":
    main()
