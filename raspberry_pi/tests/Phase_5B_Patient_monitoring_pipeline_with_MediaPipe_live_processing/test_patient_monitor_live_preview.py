import cv2
from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.patient_monitor import PatientMonitor

def main():
    print("Starting patient monitor live preview test...")
    print("Press q to quit.")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    monitor_config = config["patient_monitoring"]

    monitor = PatientMonitor(monitor_config, logger)

    if not monitor.initialize_camera():
        print("Camera initialization failed.")
        return

    monitor.initialize_mediapipe()

    while True:
        ret, frame = monitor.camera.read()
        if not ret:
            print("Failed to capture frame.")
            break

        result = monitor.process_frame(frame)

        overlay = [
            f"Swallow: {result['swallow_detected']} ({result['swallow_confidence']:.2f})",
            f"Cough: {result['cough_detected']} ({result['cough_confidence']:.2f})",
            f"Hand-to-mouth: {result['hand_motion_detected']} ({result['hand_motion_confidence']:.2f})",
        ]

        y = 30
        for line in overlay:
            cv2.putText(frame, line, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            y += 30

        cv2.imshow("Patient Monitor Live Preview", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    monitor.cleanup()
    cv2.destroyAllWindows()
    print("Patient monitor live preview test completed.")

if __name__ == "__main__":
    main()
