from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.decision_engine import DecisionEngine


def main():
    print("Starting decision engine init test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    engine = DecisionEngine(config["decision_engine"], logger)
    print("Decision engine created successfully.")


if __name__ == "__main__":
    main()
