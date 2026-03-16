from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger

def main():
    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    logger.debug("Debug test message")
    logger.info("Info test message")
    logger.warning("Warning test message")
    logger.error("Error test message")

    logger.event("phase1_test", {"status": "ok", "module": "logger"})
    logger.sensor("weight", {"station_id": "station_1", "weight_g": 12.34})

    print("Logger output test completed.")

if __name__ == "__main__":
    main()
