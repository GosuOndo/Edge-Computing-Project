import time

from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.display_manager import DisplayManager

def main():

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    display = DisplayManager(config["hardware"]["display"], logger)

    display.initialize()

    display.show_idle_screen({
        "medicine_name": "Aspirin 100mg",
        "time": "08:00",
        "time_until": "00:10:00"
    })

    start = time.time()

    while time.time() - start < 10:
        display.update()
        time.sleep(0.05)

    display.cleanup()

if __name__ == "__main__":
    main()
