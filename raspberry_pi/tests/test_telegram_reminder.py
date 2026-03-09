from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.telegram_bot import TelegramBot

def main():
    print("Starting Telegram reminder test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    telegram_config = config.get_telegram_config()

    bot = TelegramBot(telegram_config, logger)

    success = bot.send_medication_reminder(
        medicine_name="Panadol",
        dosage=2,
        time_str="12:30"
    )

    print("Reminder success:", success)
    print("Queue size:", bot.get_queue_size())
    print("Connected state:", bot.is_connected())

    bot.cleanup()
    print("Telegram reminder test completed.")

if __name__ == "__main__":
    main()
