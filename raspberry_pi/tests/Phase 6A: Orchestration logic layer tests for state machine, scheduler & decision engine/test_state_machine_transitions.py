from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.services.state_machine import StateMachine, SystemState


def main():
    print("Starting state machine transition test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    sm = StateMachine(logger)

    print("Initial state:", sm.get_state_name())
    print("Can IDLE -> REMINDER_ACTIVE:", sm.can_transition_to(SystemState.REMINDER_ACTIVE))

    sm.transition_to(SystemState.REMINDER_ACTIVE, {"medicine_name": "AMLODIPINE"})
    print("After transition:", sm.get_state_name())
    print("State data:", sm.get_state_data())

    sm.transition_to(SystemState.WAITING_FOR_INTAKE, {"station_id": "station_1"})
    print("After second transition:", sm.get_state_name())
    print("State data:", sm.get_state_data())

    sm.reset_to_idle()
    print("After reset:", sm.get_state_name())
    print("State data:", sm.get_state_data())


if __name__ == "__main__":
    main()
