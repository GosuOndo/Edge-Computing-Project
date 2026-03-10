import time
from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.patient_monitor import PatientMonitor

def monitoring_callback(detections, elapsed, duration):
    print(
        f"[{elapsed:.1f}/{duration}s] "
        f"swallow={detections['swallow_detected']} "
        f"cough={detections['cough_detected']} "
        f"hand={detections['hand_motion_detected']}"
    )

def main():
    print("Starting patient monitor callback test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    monitor_config = config["patient_monitoring"]

    monitor = PatientMonitor(monitor_config, logger)

    started = monitor.start_monitoring(duration=8, callback=monitoring_callback)
    print("Monitoring started:", started)

    if not started:
        print("Monitoring failed to start.")
        monitor.cleanup()
        return

    while monitor.is_monitoring_active():
        time.sleep(0.5)

    results = monitor.get_results()
    print("\nFinal results:")
    print(results)

    monitor.cleanup()
    print("Patient monitor callback test completed.")

if __name__ == "__main__":
    main()
