from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.services.scheduler import MedicationScheduler


def reminder_callback(data):
    print("REMINDER CALLBACK:", data)


def missed_callback(data):
    print("MISSED DOSE CALLBACK:", data)


def main():
    print("Starting direct scheduler trigger test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    sched = MedicationScheduler(config["schedule"], logger)
    sched.set_reminder_callback(reminder_callback)
    sched.set_missed_dose_callback(missed_callback)

    sched._trigger_reminder(
        medicine_name="AMLODIPINE",
        dosage=1,
        station_id="station_1",
        scheduled_time="08:00"
    )

    print("Pending reminder exists:", sched.is_pending("AMLODIPINE"))
    print("Pending reminder data:", sched.get_pending_reminder("AMLODIPINE"))


if __name__ == "__main__":
    main()
