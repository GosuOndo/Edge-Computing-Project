from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger

def main():
    print("Starting Phase 1 smoke test...")

    config = get_config("config/config.yaml")
    print("Config loaded successfully.")
    print("Top-level config keys:", list(config.config.keys()))

    logger = get_logger(config.get_logging_config())
    logger.info("Logger initialised successfully.")

    print("Phase 1 smoke test passed.")

if __name__ == "__main__":
    main()
