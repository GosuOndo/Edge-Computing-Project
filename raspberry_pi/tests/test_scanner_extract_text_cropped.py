from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.medicine_scanner import MedicineScanner
import cv2

def main():
    print("Starting OCR extraction on cropped text region...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    ocr_config = config["ocr"]

    scanner = MedicineScanner(ocr_config, logger)

    image = cv2.imread("data/test_capture_live.jpg")
    if image is None:
        print("Could not load data/test_capture_live.jpg")
        return

    h, w = image.shape[:2]

    # Keep only left side of the label, where the medicine text is
    cropped = image[:, :int(w * 0.48)]

    cv2.imwrite("data/test_capture_live_cropped.jpg", cropped)
    print("Saved cropped image to data/test_capture_live_cropped.jpg")

    processed = scanner.preprocess_image(cropped)
    cv2.imwrite("data/test_capture_live_cropped_preprocessed.jpg", processed)
    print("Saved processed cropped image to data/test_capture_live_cropped_preprocessed.jpg")

    result = scanner.extract_text(processed)
    print("OCR result on cropped region:")
    print(result)

if __name__ == "__main__":
    main()
