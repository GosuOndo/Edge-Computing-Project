from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.weight_manager import WeightManager

def on_removal(event):
    print("REMOVAL CALLBACK:", event)

def on_addition(event):
    print("ADDITION CALLBACK:", event)

def main():
    print("Starting direct weight manager test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    weight_config = config["weight_sensors"]

    manager = WeightManager(weight_config, logger)
    manager.set_pill_removal_callback(on_removal)
    manager.set_pill_addition_callback(on_addition)

    # Simulate stable starting state
    payload1 = {
        "station_id": "station_1",
        "weight_g": 10.0,
        "stable": True,
        "delta_g": 0.0,
        "timestamp": 1000.0
    }

    # Simulate pill removal
    payload2 = {
        "station_id": "station_1",
        "weight_g": 9.5,
        "stable": True,
        "delta_g": -0.5,
        "timestamp": 1001.0
    }

    # Simulate pill addition
    payload3 = {
        "station_id": "station_1",
        "weight_g": 10.5,
        "stable": True,
        "delta_g": 0.5,
        "timestamp": 1002.0
    }

    manager.process_weight_data(payload1)
    manager.process_weight_data(payload2)
    manager.process_weight_data(payload3)

    print("Current weight:", manager.get_current_weight("station_1"))
    print("Stable:", manager.is_stable("station_1"))
    print("Estimated pill count at 10.5g:", manager.estimate_pill_count("station_1", 10.5))
    print("Direct test completed.")

if __name__ == "__main__":
    main()
