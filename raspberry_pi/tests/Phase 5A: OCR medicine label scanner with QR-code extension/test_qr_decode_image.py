import cv2
from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.qr_scanner import QRScanner


def main():
    print("Starting QR decode image test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    scanner = QRScanner(logger)

    image = cv2.imread("data/test_capture_live.jpg")
    if image is None:
        print("Could not load data/test_capture_live.jpg")
        return

    results = scanner.decode_image(image)

    print(f"\nQR results found: {len(results)}")

    if not results:
        print("No QR codes detected.")
        return

    for i, result in enumerate(results, start=1):
        print(f"\nResult {i}:")
        print(f"Type: {result['type']}")
        print(f"Rect: {result['rect']}")
        print("Data:")
        print(result["data"])


if __name__ == "__main__":
    main()
