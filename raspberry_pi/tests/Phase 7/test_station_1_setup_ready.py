import time

from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.services.mqtt_client import MQTTClient
from raspberry_pi.modules.weight_manager import WeightManager


def main():
    print("Starting Station 1 setup / readiness test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    mqtt_client = MQTTClient(config["mqtt"], logger)
    weight_manager = WeightManager(config["weight_sensors"], logger)

    station_id = "station_1"

    def handle_weight(data):
        logger.info(f"[RAW WEIGHT] {data}")
        weight_manager.process_weight_data(data)

    def handle_status(data):
        logger.info(f"[STATUS] {data}")

    mqtt_client.set_weight_callback(handle_weight)
    mqtt_client.set_status_callback(handle_status)

    mqtt_client.connect()

    print("\nStep 1: Place the FULL medicine container on station_1.")
    print("Step 2: Wait until the reading is stable.")
    print("Step 3: Press Enter here to capture the baseline.")
    print("Step 4: This script will confirm station readiness.\n")

    try:
        # Wait until we have some live data
        print("Waiting for first live weight data...")
        start = time.time()
        while weight_manager.get_current_weight(station_id) is None:
            if time.time() - start > 20:
                print("Timeout waiting for live station data.")
                return
            time.sleep(0.2)

        status = weight_manager.get_station_status(station_id)
        print("Initial station status:")
        print(status)

        input("\nPress Enter to capture baseline for station_1...")

        ok = weight_manager.capture_current_baseline(station_id)
        if not ok:
            print("Baseline capture failed.")
            print("Make sure the container is on the station and the reading is stable.")
            return

        print("\nBaseline captured successfully.")
        print("Waiting 2 seconds for final status...")

        time.sleep(2)

        final_status = weight_manager.get_station_status(station_id)
        print("\nFinal station status:")
        print(final_status)

        if (
            final_status["connected"]
            and final_status["stable"]
            and final_status["baseline_g"] is not None
            and not final_status["needs_baseline_capture"]
        ):
            print("\nStation 1 is READY for runtime.")
        else:
            print("\nStation 1 is NOT fully ready yet.")
            print("Check the status fields above.")

    except KeyboardInterrupt:
        print("\nStopped by user.")

    finally:
        mqtt_client.disconnect()
        print("Setup / readiness test completed.")


if __name__ == "__main__":
    main()
