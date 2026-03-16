import cv2
from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.qr_scanner import QRScanner


def main():
    print("Starting live QR camera test...")
    print("Show the QR label to the camera. Press q to quit.")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    scanner = QRScanner(logger)

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Failed to open camera.")
        return

    detected_once = False
    last_text = None

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Failed to capture frame.")
            break

        results = scanner.decode_image(frame)

        for result in results:
            rect = result["rect"]
            x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]

            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

            if result["data"] != last_text:
                print("\nQR detected:")
                print(result["data"])

                parsed = scanner.parse_qr_text(result["data"])
                print("\nParsed fields:")
                print(parsed)

                validation = scanner.validate_required_fields(parsed)
                print("\nValidation:")
                print(validation)

                last_text = result["data"]

            detected_once = True

        cv2.imshow("Live QR Scan", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()

    print("\nDetected at least one QR:", detected_once)
    print("Live QR camera test completed.")


if __name__ == "__main__":
    main()
