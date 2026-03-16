from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.patient_monitor import PatientMonitor

def main():
    print("Starting patient monitor single-frame test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    monitor_config = config["patient_monitoring"]

    monitor = PatientMonitor(monitor_config, logger)

    if not monitor.initialize_camera():
        print("Camera initialization failed.")
        return

    monitor.initialize_mediapipe()

    ret, frame = monitor.camera.read()
    if not ret:
        print("Failed to capture frame.")
        monitor.cleanup()
        return

    result = monitor.process_frame(frame)

    print("Single-frame detection result:")
    print(result)

    monitor.cleanup()
    print("Patient monitor single-frame test completed.")

if __name__ == "__main__":
    main()
