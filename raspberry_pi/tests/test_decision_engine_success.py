from raspberry_pi.utils.config_loader import get_config
from raspberry_pi.utils.logger import get_logger
from raspberry_pi.modules.decision_engine import DecisionEngine


def main():
    print("Starting decision engine success test...")

    config = get_config("config/config.yaml")
    logger = get_logger(config.get_logging_config())

    engine = DecisionEngine(config["decision_engine"], logger)

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

    print(decision)
    print(engine.get_alert_messages(decision))
    print("Should alert caregiver:", engine.should_alert_caregiver(decision))


if __name__ == "__main__":
    main()
