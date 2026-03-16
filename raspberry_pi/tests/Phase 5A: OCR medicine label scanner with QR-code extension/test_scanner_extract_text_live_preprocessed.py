from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.medicine_scanner import MedicineScanner
import cv2

def main():
    print("Starting OCR extraction on preprocessed live image...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    ocr_config = config["ocr"]

    scanner = MedicineScanner(ocr_config, logger)

    image = cv2.imread("data/test_capture_live_preprocessed.jpg", cv2.IMREAD_GRAYSCALE)
    if image is None:
        print("Could not load data/test_capture_live_preprocessed.jpg")
        return

    result = scanner.extract_text(image)
    print("OCR result on preprocessed image:")
    print(result)

if __name__ == "__main__":
    main()
