import logging
import time
import yaml

from raspberry_pi.services.mqtt_client import MQTTClient
from raspberry_pi.modules.weight_manager import WeightManager


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    logger = logging.getLogger("station1_dose_test")

    with open("config/config.yaml", "r") as f:
        config = yaml.safe_load(f)

    mqtt_client = MQTTClient(config["mqtt"], logger)
    weight_manager = WeightManager(config["weight_sensors"], logger)

    expected_station = "station_1"
    expected_dose = 2

    def on_removal(event):
        logger.info(f"[REMOVAL EVENT] {event}")

        if event["station_id"] == expected_station:
            result = weight_manager.verify_dosage(
                station_id=expected_station,
                expected_pills=expected_dose,
                tolerance=0
            )
            logger.info(f"[DOSAGE CHECK] {result}")

    def on_addition(event):
        logger.info(f"[ADDITION EVENT] {event}")

    def handle_weight(data):
        logger.info(f"[RAW WEIGHT] {data}")
        weight_manager.process_weight_data(data)

    def handle_status(data):
        logger.info(f"[STATUS] {data}")

    weight_manager.set_pill_removal_callback(on_removal)
    weight_manager.set_pill_addition_callback(on_addition)

    mqtt_client.set_weight_callback(handle_weight)
    mqtt_client.set_status_callback(handle_status)

    mqtt_client.connect()

    logger.info("Station 1 dosage test running.")
    logger.info("Step 1: place full Aspirin container on station_1.")
    logger.info("Step 2: wait for stable reading.")
    logger.info("Step 3: press Enter here to capture baseline.")
    logger.info("Step 4: remove 2 pills and return container.")
    logger.info("The script will verify whether the detected dose matches 2 pills.")

    try:
        input("Press Enter to capture Station 1 baseline...")
        ok = weight_manager.capture_current_baseline(expected_station)

        if not ok:
            logger.warning("Baseline capture failed. Make sure station_1 is stable.")
            return

        logger.info("Baseline captured successfully.")
        logger.info("Now remove 2 pills and return the container. Press Ctrl+C to stop.")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Stopping Station 1 dosage test...")
    finally:
        mqtt_client.disconnect()


if __name__ == "__main__":
    main()
