from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.telegram_bot import TelegramBot

def main():
    print("Starting TelegramBot init test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    telegram_config = config.get_telegram_config()

    bot = TelegramBot(telegram_config, logger)

    print("TelegramBot created successfully.")
    print("Enabled:", bot.enabled)
    print("Queue size:", bot.get_queue_size())
    print("Connected state:", bot.is_connected())

    bot.cleanup()
    print("TelegramBot init test completed.")

if __name__ == "__main__":
    main()
