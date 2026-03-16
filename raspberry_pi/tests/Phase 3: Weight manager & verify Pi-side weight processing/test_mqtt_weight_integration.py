from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.services.mqtt_client import MQTTClient
from raspberry_pi.modules.weight_manager import WeightManager
import time

def on_removal(event):
    print("REMOVAL EVENT:", event)

def on_addition(event):
    print("ADDITION EVENT:", event)

def main():
    print("Starting MQTT + WeightManager integration test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    mqtt_config = config.get_mqtt_config()
    weight_config = config["weight_sensors"]

    manager = WeightManager(weight_config, logger)
    manager.set_pill_removal_callback(on_removal)
    manager.set_pill_addition_callback(on_addition)

    client = MQTTClient(mqtt_config, logger)
    client.set_weight_callback(manager.process_weight_data)
    client.connect()

    print("Connected. Waiting 20 seconds for incoming weight messages...")
    time.sleep(20)

    print("Latest station data:")
    print(manager.weight_data)

    client.disconnect()
    print("MQTT + WeightManager integration test completed.")

if __name__ == "__main__":
    main()
