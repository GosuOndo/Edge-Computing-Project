from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.services.mqtt_client import MQTTClient
import time

received_messages = []

def weight_callback(data):
    print("Weight callback triggered:")
    print(data)
    received_messages.append(("weight", data))

def status_callback(data):
    print("Status callback triggered:")
    print(data)
    received_messages.append(("status", data))

def main():
    print("Starting MQTT receive test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    mqtt_config = config.get_mqtt_config()

    client = MQTTClient(mqtt_config, logger)
    client.set_weight_callback(weight_callback)
    client.set_status_callback(status_callback)
    client.connect()

    print("Client connected. Waiting 15 seconds for test messages...")
    print("Publish a test message from another terminal now.")
    time.sleep(15)

    client.disconnect()

    print("Messages received:", received_messages)
    print("MQTT receive test completed.")

if __name__ == "__main__":
    main()
