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
    logger = logging.getLogger("model_b_test")

    with open("config/config.yaml", "r") as f:
        config = yaml.safe_load(f)

    mqtt_client = MQTTClient(config["mqtt"], logger)
    weight_manager = WeightManager(config["weight_sensors"], logger)

    def on_removal(event):
        logger.info(f"[REMOVAL EVENT] {event}")

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

    logger.info("Model B test running.")
    logger.info("Phase 1: leave station empty and stable.")
    logger.info("Phase 2: place full container on station.")
    logger.info("When stable, press Enter here to capture container baseline.")

    try:
        input("Press Enter to capture baseline for station_1...")
        ok = weight_manager.capture_current_baseline("station_1")
        if ok:
            logger.info("Baseline captured successfully for station_1.")
        else:
            logger.warning("Baseline capture failed. Make sure the reading is stable.")
            return

        logger.info("Now perform your test:")
        logger.info("- remove 1 pill and return container")
        logger.info("- remove 2 pills and return container")
        logger.info("- add pills back if needed")
        logger.info("Press Ctrl+C to stop.")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Stopping Model B test...")
    finally:
        mqtt_client.disconnect()


if __name__ == "__main__":
    main()
