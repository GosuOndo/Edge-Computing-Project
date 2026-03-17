from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.database import Database
from raspberry_pi.modules.medicine_scanner import MedicineScanner
from raspberry_pi.modules.tag_runtime_service import TagRuntimeService
from raspberry_pi.modules.identity_manager import IdentityManager


def main():
    print("Starting identity manager tag-timeout fallback test...")
    print("Do NOT tap the sticker. Let tag attempts fail so fallback can proceed.")
    print("Then present a QR or OCR-readable label to the camera if available.")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    db = Database(config["database"], logger)
    db.connect()

    scanner_config = dict(config["ocr"])
    scanner_config.update(config["hardware"].get("camera", {}))
    scanner = MedicineScanner(scanner_config, logger)

    tag_topic = config["identity"]["tag"]["mqtt_topic"]
    tag_runtime = TagRuntimeService(
        mqtt_config=config["mqtt"],
        database=db,
        logger=logger,
        topic=tag_topic
    )
    tag_runtime.start()

    identity_manager = IdentityManager(
        config=config,
        scanner=scanner,
        database=db,
        tag_runtime_service=tag_runtime,
        logger=logger
    )

    try:

        result = identity_manager.verify_identity(
            expected_medicine_id="M001",
            expected_medicine_name="Aspirin 100mg",
            expected_station_id="station_1"
        )

        print("\nIdentity result:")
        print(result)

    finally:
        scanner.release_camera()
        tag_runtime.stop()
        db.cleanup()
        print("\nIdentity manager tag-timeout fallback test completed.")


if __name__ == "__main__":
    main()
