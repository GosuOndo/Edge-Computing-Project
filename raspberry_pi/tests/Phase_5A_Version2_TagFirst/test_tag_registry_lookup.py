from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.database import Database


def main():
    print("Starting tag registry lookup test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    db = Database(config["database"], logger)
    db.connect()

    try:
        records = db.list_registered_medicines()
        print("\nAll registered medicines:")
        for r in records:
            print(r)

        print("\nLookup by medicine_id = M001")
        print(db.get_registered_medicine_by_id("M001"))

        print("\nLookup by station_id = station_1")
        print(db.get_registered_medicine_by_station("station_1"))

        print("\nLookup by tag_uid = 04B1F1A7772681")
        print(db.get_registered_medicine_by_tag_uid("04B1F1A7772681"))

    finally:
        db.cleanup()
        print("\nTag registry lookup test completed.")


if __name__ == "__main__":
    main()
