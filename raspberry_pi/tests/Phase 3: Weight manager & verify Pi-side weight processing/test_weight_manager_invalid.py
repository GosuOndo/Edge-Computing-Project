from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.weight_manager import WeightManager

def main():
    print("Starting invalid payload test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    weight_config = config["weight_sensors"]

    manager = WeightManager(weight_config, logger)

    bad_payload = {
        "weight_g": 8.0,
        "stable": True,
        "delta_g": -0.5,
        "timestamp": 1000.0
    }

    manager.process_weight_data(bad_payload)
    print("Invalid payload test completed.")

if __name__ == "__main__":
    main()
