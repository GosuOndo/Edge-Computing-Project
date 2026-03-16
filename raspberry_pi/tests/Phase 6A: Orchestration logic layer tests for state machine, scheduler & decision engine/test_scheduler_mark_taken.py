from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.services.scheduler import MedicationScheduler


def main():
    print("Starting scheduler mark-taken test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    sched = MedicationScheduler(config["schedule"], logger)

    sched._trigger_reminder(
        medicine_name="AMLODIPINE",
        dosage=1,
        station_id="station_1",
        scheduled_time="08:00"
    )

    print("Before mark taken:", sched.is_pending("AMLODIPINE"))
    sched.mark_dose_taken("AMLODIPINE")
    print("After mark taken:", sched.is_pending("AMLODIPINE"))


if __name__ == "__main__":
    main()
