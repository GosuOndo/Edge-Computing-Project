import time
from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.patient_monitor import PatientMonitor

def main():
    print("Starting patient monitor session test...")
    print("Sit in front of the camera and perform some natural motions.")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    monitor_config = config["patient_monitoring"]

    monitor = PatientMonitor(monitor_config, logger)

    started = monitor.start_monitoring(duration=10)
    print("Monitoring started:", started)

    if not started:
        print("Monitoring failed to start.")
        monitor.cleanup()
        return

    while monitor.is_monitoring_active():
        print("Monitoring active...")
        time.sleep(1)

    results = monitor.get_results()

    print("\nFinal monitoring results:")
    print(results)

    monitor.cleanup()
    print("Patient monitor session test completed.")

if __name__ == "__main__":
    main()
