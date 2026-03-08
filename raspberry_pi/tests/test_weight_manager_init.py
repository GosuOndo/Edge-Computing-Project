from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.weight_manager import WeightManager

def main():
    print("Starting WeightManager init test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    weight_config = config["weight_sensors"]

    manager = WeightManager(weight_config, logger)

    print("WeightManager created successfully.")
    print("Configured stations:", list(manager.station_configs.keys()))
    print("History buffers created for:", list(manager.weight_history.keys()))

if __name__ == "__main__":
    main()
