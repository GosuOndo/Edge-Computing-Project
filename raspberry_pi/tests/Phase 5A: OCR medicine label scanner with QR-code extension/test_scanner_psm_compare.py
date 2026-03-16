from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.medicine_scanner import MedicineScanner
import cv2
import pytesseract

def main():
    print("Starting OCR PSM comparison test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    ocr_config = config["ocr"]

    scanner = MedicineScanner(ocr_config, logger)

    image = cv2.imread("data/test_capture_live_cropped.jpg")
    if image is None:
        print("Could not load data/test_capture_live_cropped.jpg")
        return

    processed = scanner.preprocess_image(image)

    for psm in [6, 11, 4]:
        print(f"\nTesting PSM {psm}")
        result = pytesseract.image_to_string(
            processed,
            lang=scanner.ocr_language,
            config=f"--psm {psm}"
        )
        print(result)

if __name__ == "__main__":
    main()
