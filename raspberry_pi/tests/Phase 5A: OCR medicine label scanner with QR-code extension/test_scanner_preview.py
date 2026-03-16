import cv2
from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.medicine_scanner import MedicineScanner

def main():
    print("Starting live camera preview test...")
    print("Press q to quit, s to save a frame.")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    ocr_config = config["ocr"]

    scanner = MedicineScanner(ocr_config, logger)

    if not scanner.initialize_camera():
        print("Camera initialization failed.")
        return

    while True:
        frame = scanner.capture_frame()
        if frame is None:
            print("Failed to capture frame.")
            break

        cv2.imshow("Medicine Scanner Preview", frame)
        key = cv2.waitKey(1) & 0xFF

        if key == ord('s'):
            cv2.imwrite("data/test_capture_live.jpg", frame)
            print("Saved frame to data/test_capture_live.jpg")

        elif key == ord('q'):
            break

    scanner.release_camera()
    cv2.destroyAllWindows()
    print("Preview test completed.")

if __name__ == "__main__":
    main()
