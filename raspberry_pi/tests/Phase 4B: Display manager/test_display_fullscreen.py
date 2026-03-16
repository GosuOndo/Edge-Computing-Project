from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.display_manager import DisplayManager
import time

def main():
    print("Starting fullscreen display test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    display_config = {
        "width": 1024,
        "height": 600,
        "fullscreen": True
    }

    display = DisplayManager(display_config, logger)

    if not display.initialize():
        print("Display failed to initialize.")
        return

    display.show_reminder_screen("Panadol", 2, "12:30")
    time.sleep(5)

    display.cleanup()
    print("Fullscreen display test completed.")

if __name__ == "__main__":
    main()
