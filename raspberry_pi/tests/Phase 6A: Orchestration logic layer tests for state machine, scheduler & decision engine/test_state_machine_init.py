from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.services.state_machine import StateMachine

def main():
    print("\n=== State Machine Init Test ===\n")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    sm = StateMachine(logger)

    print("Current state:", sm.get_state_name())
    print("Is idle:", sm.is_idle())
    print("Is busy:", sm.is_busy())
    print("State data:", sm.get_state_data())

if __name__ == "__main__":
    main()
