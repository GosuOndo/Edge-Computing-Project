from raspberry_pi.utils.config_loader import get_config

def main():
    config = get_config("config/config.yaml")

    print("System name:", config.get("system.name", "NOT FOUND"))
    print("MQTT broker:", config.get("mqtt.broker_host", "NOT FOUND"))
    print("Telegram enabled:", config.get("telegram.enabled", "NOT FOUND"))
    print("Logging level:", config.get("logging.level", "NOT FOUND"))

if __name__ == "__main__":
    main()
