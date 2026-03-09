from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.display_manager import DisplayManager
import time

def main():
    print("Starting display init test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    # Use a safe local config for testing
    display_config = {
        "width": 1024,
        "height": 600,
        "fullscreen": False
    }

    display = DisplayManager(display_config, logger)
    success = display.initialize()

    print("Display initialized:", success)
    print("Display manager state:", display.initialized)

    time.sleep(3)
    display.cleanup()

    print("Display init test completed.")

if __name__ == "__main__":
    main()
