from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.telegram_bot import TelegramBot

def main():
    print("Starting Telegram direct send test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())
    telegram_config = config.get_telegram_config()

    bot = TelegramBot(telegram_config, logger)

    patient_id = telegram_config.get("patient_chat_id")
    success = bot.send_message(patient_id, "Phase 4A test message from Smart Medication System")

    print("Send success:", success)
    print("Queue size after send:", bot.get_queue_size())
    print("Connected state:", bot.is_connected())

    bot.cleanup()
    print("Telegram direct send test completed.")

if __name__ == "__main__":
    main()
