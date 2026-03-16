import time
from copy import deepcopy
from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.services.scheduler import MedicationScheduler


def missed_callback(data):
    print("MISSED DOSE CALLBACK:", data)


def main():
    print("Starting scheduler missed-dose test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    schedule_config = deepcopy(config["schedule"])
    schedule_config["reminder"]["timeout_minutes"] = 0.05  # about 3 seconds

    sched = MedicationScheduler(schedule_config, logger)
    sched.set_missed_dose_callback(missed_callback)

    sched._trigger_reminder(
        medicine_name="AMLODIPINE",
        dosage=1,
        station_id="station_1",
        scheduled_time="08:00"
    )

    print("Waiting for missed dose callback...")
    time.sleep(5)


if __name__ == "__main__":
    main()
