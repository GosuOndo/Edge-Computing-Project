from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.services.state_machine import StateMachine, SystemState


def on_reminder(data):
    print("REMINDER CALLBACK:", data)


def on_alert(data):
    print("ALERT CALLBACK:", data)


def main():
    print("Starting state machine callback test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    sm = StateMachine(logger)
    sm.register_state_callback(SystemState.REMINDER_ACTIVE, on_reminder)
    sm.register_state_callback(SystemState.ALERTING, on_alert)

    sm.transition_to(SystemState.REMINDER_ACTIVE, {"medicine_name": "AMLODIPINE"})
    sm.transition_to(SystemState.ALERTING, {"reason": "incorrect_dosage"})


if __name__ == "__main__":
    main()
