from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.display_manager import DisplayManager
import time

def main():
    print("Starting display screen test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    display_config = {
        "width": 1024,
        "height": 600,
        "fullscreen": False
    }

    display = DisplayManager(display_config, logger)

    if not display.initialize():
        print("Display failed to initialize.")
        return

    print("Showing idle screen...")
    display.show_idle_screen(next_medication="Panadol at 12:30")
    time.sleep(3)

    print("Showing reminder screen...")
    display.show_reminder_screen("Panadol", 2, "12:30")
    time.sleep(3)

    print("Showing monitoring screen...")
    display.show_monitoring_screen(elapsed=10, duration=30, status="Monitoring...")
    time.sleep(3)

    print("Showing success screen...")
    display.show_success_screen("Panadol", "Dose verified successfully")
    time.sleep(3)

    print("Showing warning screen...")
    display.show_warning_screen("Dosage Warning", "Please verify the number of pills")
    time.sleep(3)

    print("Showing error screen...")
    display.show_error_screen("Camera unavailable")
    time.sleep(3)

    display.cleanup()
    print("Display screen test completed.")

if __name__ == "__main__":
    main()
