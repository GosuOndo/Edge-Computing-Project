from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.patient_monitor import PatientMonitor

def main():
    print("Starting patient monitor init test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    monitor_config = config["patient_monitoring"]

    monitor = PatientMonitor(monitor_config, logger)

    camera_ok = monitor.initialize_camera()
    print("Camera initialized:", camera_ok)
    print("Camera ready:", monitor.camera_ready)

    monitor.initialize_mediapipe()
    print("MediaPipe initialized successfully.")

    monitor.cleanup()
    print("Patient monitor init test completed.")

if __name__ == "__main__":
    main()
