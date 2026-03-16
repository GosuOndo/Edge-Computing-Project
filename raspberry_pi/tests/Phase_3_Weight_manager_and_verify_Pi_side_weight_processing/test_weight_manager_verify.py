from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.weight_manager import WeightManager

def main():
    print("Starting dosage verification test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    weight_config = config["weight_sensors"]

    manager = WeightManager(weight_config, logger)

    # Simulate a removal event corresponding to 1 pill
    payload = {
        "station_id": "station_1",
        "weight_g": 9.5,
        "stable": True,
        "delta_g": -0.5,
        "timestamp": 1000.0
    }

    manager.process_weight_data(payload)

    result = manager.verify_dosage("station_1", expected_pills=1, tolerance=1)
    print("Verification result:", result)

if __name__ == "__main__":
    main()
