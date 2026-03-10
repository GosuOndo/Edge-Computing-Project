import cv2
from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.qr_scanner import QRScanner


def main():
    print("Starting QR parse image test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    scanner = QRScanner(logger)

    image = cv2.imread("data/test_capture_live.jpg")
    if image is None:
        print("Could not load data/test_capture_live.jpg")
        return

    result = scanner.decode_and_parse(image)

    if result is None:
        print("No QR code detected.")
        return

    print("\nRaw QR text:")
    print(result["raw_text"])

    print("\nParsed QR dictionary:")
    print(result["parsed"])

    validation = scanner.validate_required_fields(result["parsed"])

    print("\nValidation result:")
    print(validation)

    if not result["parsed"]:
        print("\nWARNING: QR was detected, but no structured fields were parsed.")
        print("This usually means the QR contains a URL or unsupported text format, not medication JSON or key=value data.")


if __name__ == "__main__":
    main()
