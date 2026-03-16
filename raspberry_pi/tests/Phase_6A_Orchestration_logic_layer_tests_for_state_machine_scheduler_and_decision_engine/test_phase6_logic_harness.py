from copy import deepcopy
import time
from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.services.state_machine import StateMachine, SystemState
from raspberry_pi.services.scheduler import MedicationScheduler
from raspberry_pi.modules.decision_engine import DecisionEngine


def reminder_callback(data):
    print("REMINDER TRIGGERED:", data)


def missed_callback(data):
    print("MISSED DOSE:", data)


def main():
    print("Starting Phase 6 logic harness...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    sm = StateMachine(logger)
    engine = DecisionEngine(config["decision_engine"], logger)

    schedule_config = deepcopy(config["schedule"])
    schedule_config["reminder"]["timeout_minutes"] = 0.05
    sched = MedicationScheduler(schedule_config, logger)
    sched.set_reminder_callback(reminder_callback)
    sched.set_missed_dose_callback(missed_callback)

    # Step 1: simulate reminder state
    sm.transition_to(SystemState.REMINDER_ACTIVE, {
        "medicine_name": "AMLODIPINE",
        "dosage": 1,
        "station_id": "station_1"
    })
    print("State after reminder:", sm.get_state_name(), sm.get_state_data())

    # Step 2: simulate reminder callback directly
    sched._trigger_reminder("AMLODIPINE", 1, "station_1", "08:00")

    # Step 3: simulate verification stage
    sm.transition_to(SystemState.VERIFYING, {
        "medicine_name": "AMLODIPINE"
    })

    decision = engine.verify_medication_intake(
        expected_medicine="AMLODIPINE",
        expected_dosage=1,
        ocr_result={
            "success": True,
            "medicine_name": "AMLODIPINE",
            "confidence": 0.95
        },
        weight_result={
            "verified": True,
            "actual": 1
        },
        monitoring_result={
            "compliance_status": "good",
            "swallow_count": 1,
            "cough_count": 0,
            "hand_motion_count": 1
        }
    )

    print("Decision result:", decision["result"].value)
    print("Verified:", decision["verified"])

    if decision["verified"]:
        sched.mark_dose_taken("AMLODIPINE")
        sm.transition_to(SystemState.IDLE, {"outcome": "success"})
    else:
        sm.transition_to(SystemState.ALERTING, {"decision": decision})

    print("Final state:", sm.get_state_name(), sm.get_state_data())

    time.sleep(4)


if __name__ == "__main__":
    main()
