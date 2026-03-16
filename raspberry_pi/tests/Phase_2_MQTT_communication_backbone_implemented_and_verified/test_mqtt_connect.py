from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.services.mqtt_client import MQTTClient
import time

def main():
    print("Starting MQTT connection test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    mqtt_config = config.get_mqtt_config()

    print("MQTT config loaded:")
    print(mqtt_config)

    client = MQTTClient(mqtt_config, logger)
    client.connect()

    print("Connected state:", client.is_connected())

    time.sleep(2)
    client.disconnect()

    print("MQTT connection test completed.")

if __name__ == "__main__":
    main()
