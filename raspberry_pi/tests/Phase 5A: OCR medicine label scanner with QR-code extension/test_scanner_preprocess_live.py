from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.medicine_scanner import MedicineScanner
import cv2

def main():
    print("Starting live-image preprocess test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    ocr_config = config["ocr"]

    scanner = MedicineScanner(ocr_config, logger)

    frame = cv2.imread("data/test_capture_live.jpg")
    if frame is None:
        print("Could not load data/test_capture_live.jpg")
        return

    processed = scanner.preprocess_image(frame)
    cv2.imwrite("data/test_capture_live_preprocessed.jpg", processed)

    print("Saved processed image to data/test_capture_live_preprocessed.jpg")

if __name__ == "__main__":
    main()
