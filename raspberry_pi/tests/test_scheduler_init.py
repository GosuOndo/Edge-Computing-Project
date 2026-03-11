from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.services.scheduler import MedicationScheduler


def main():
    print("Starting scheduler init test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    sched = MedicationScheduler(config["schedule"], logger)

    print("Scheduler created successfully.")
    print("Today's schedule:")
    print(sched.get_todays_schedule())


if __name__ == "__main__":
    main()
