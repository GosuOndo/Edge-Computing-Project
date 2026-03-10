import cv2
from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.qr_scanner import QRScanner
from raspberry_pi.modules.medicine_scanner import MedicineScanner


def main():
    print("Starting QR-first / OCR-fallback label reader test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    qr_scanner = QRScanner(logger)
    ocr_scanner = MedicineScanner(config["ocr"], logger)

    image = cv2.imread("data/test_capture_live.jpg")
    if image is None:
        print("Could not load data/test_capture_live.jpg")
        return

    qr_result = qr_scanner.decode_and_parse(image)

    if qr_result is not None:
        parsed = qr_result["parsed"]
        validation = qr_scanner.validate_required_fields(parsed)

        if validation["valid"]:
            print("\nQR SUCCESS")
            print(parsed)

            verify_result = qr_scanner.verify_medicine(parsed, "AMLODIPINE")
            print("\nVerification result:")
            print(verify_result)
            return
        else:
            print("\nQR detected, but structured medication fields are incomplete.")
            print(validation)
            print("Falling back to OCR...")
    else:
        print("\nNo QR found. Falling back to OCR...")

    processed = ocr_scanner.preprocess_image(image)
    ocr_result = ocr_scanner.extract_text(processed)

    print("\nOCR fallback result:")
    print(ocr_result)


if __name__ == "__main__":
    main()
