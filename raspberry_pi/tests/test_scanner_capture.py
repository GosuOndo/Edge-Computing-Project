from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.medicine_scanner import MedicineScanner
import cv2

def main():
    print("Starting scanner capture test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    ocr_config = config["ocr"]

    scanner = MedicineScanner(ocr_config, logger)

    if not scanner.initialize_camera():
        print("Camera initialization failed.")
        return

    frame = scanner.capture_frame()

    if frame is None:
        print("Frame capture failed.")
    else:
        print("Frame captured successfully.")
        print("Frame shape:", frame.shape)
        cv2.imwrite("data/test_capture.jpg", frame)
        print("Saved frame to data/test_capture.jpg")

    scanner.release_camera()
    print("Scanner capture test completed.")

if __name__ == "__main__":
    main()
