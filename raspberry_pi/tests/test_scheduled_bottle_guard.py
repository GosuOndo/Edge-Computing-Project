import logging
import time
from datetime import datetime
from pathlib import Path
import sys
import types
from enum import Enum, auto

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from raspberry_pi.modules.weight_manager import WeightManager


class StubSystemState(Enum):
    IDLE = auto()
    REMINDER_ACTIVE = auto()
    WAITING_FOR_INTAKE = auto()
    MONITORING_PATIENT = auto()
    VERIFYING = auto()
    ALERTING = auto()
    ERROR = auto()
    SETUP = auto()


class StubDecisionResult(Enum):
    SUCCESS = "success"
    INCORRECT_DOSAGE = "incorrect_dosage"
    WRONG_MEDICINE = "wrong_medicine"
    BEHAVIORAL_ISSUE = "behavioral_issue"
    NO_INTAKE = "no_intake"
    SENSOR_ERROR = "sensor_error"
    PARTIAL_SUCCESS = "partial_success"


def _register_stub_module(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


_register_stub_module("utils.logger", get_logger=lambda *_args, **_kwargs: None)
_register_stub_module("utils.config_loader", get_config=lambda *_args, **_kwargs: None)
_register_stub_module("services.mqtt_client", MQTTClient=object)
_register_stub_module("services.scheduler", MedicationScheduler=object)
_register_stub_module(
    "services.state_machine",
    StateMachine=object,
    SystemState=StubSystemState,
)
_register_stub_module("modules.weight_manager", WeightManager=object)
_register_stub_module("modules.medicine_scanner", MedicineScanner=object)
_register_stub_module("modules.patient_monitor", PatientMonitor=object)
_register_stub_module("modules.telegram_bot", TelegramBot=object)
_register_stub_module("modules.display_manager", DisplayManager=object)
_register_stub_module("modules.audio_manager", AudioManager=object)
_register_stub_module(
    "modules.decision_engine",
    DecisionEngine=object,
    DecisionResult=StubDecisionResult,
)
_register_stub_module("modules.database", Database=object)
_register_stub_module("modules.tag_runtime_service", TagRuntimeService=object)
_register_stub_module("modules.identity_manager", IdentityManager=object)
_register_stub_module("modules.registration_manager", RegistrationManager=object)

from raspberry_pi.main import MedicationSystem, SystemState


class DummyLogger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def critical(self, *args, **kwargs):
        pass


class FakeDatabase:
    def __init__(self, record=None):
        self.record = record

    def get_registered_medicine_by_tag_uid(self, tag_uid):
        return self.record


class FakeTagManager:
    def __init__(self, record=None):
        self.record = record

    def build_record_from_scan(self, scan_msg):
        return self.record


class FakeTagRuntimeService:
    def __init__(self, latest=None, record=None):
        self.latest = latest
        self.tag_manager = FakeTagManager(record)

    def get_latest_scan(self):
        return self.latest


class FakeWeightManager:
    def __init__(self, status):
        self.status = status
        self.calls = []
        self.baseline_weights = {"station_1": status.get("weight_g", 0.0)}

    def get_station_status(self, station_id):
        return dict(self.status)

    def capture_current_baseline(self, station_id):
        self.calls.append(("capture", station_id))
        self.baseline_weights[station_id] = self.status.get("weight_g", 0.0)
        return True

    def enable_event_detection(self, station_id):
        self.calls.append(("enable", station_id))
        self.status["event_detection_enabled"] = True


class FakeStateMachine:
    def __init__(self, state):
        self.state = state

    def get_state(self):
        return self.state


class FakeTelegram:
    def __init__(self):
        self.alerts = []

    def send_unauthorized_bottle_movement_alert(self, **kwargs):
        self.alerts.append(kwargs)
        return True


def make_system():
    system = MedicationSystem.__new__(MedicationSystem)
    system.logger = DummyLogger()
    system.current_medication = None
    system.secured_medications = {}
    system._processed_tag_scans = {}
    system.min_secured_bottle_weight_g = 5.0
    system.telegram = FakeTelegram()
    return system


def test_process_secured_bottle_placement_tracks_next_due():
    record = {
        "medicine_id": "M001",
        "medicine_name": "Aspirin 100mg",
        "station_id": "station_1",
        "tag_uid": "TAG123",
        "time_slots": "08:00,20:00",
    }
    latest = {
        "received_at": 123.0,
        "scan_msg": {"tag_uid": "TAG123"},
    }

    system = make_system()
    system.database = FakeDatabase(record)
    system.tag_runtime_service = FakeTagRuntimeService(latest=latest, record=record)
    system.weight_manager = FakeWeightManager({
        "connected": True,
        "stable": True,
        "weight_g": 42.5,
        "event_detection_enabled": False,
    })
    fixed_due = datetime(2026, 3, 25, 20, 0, 0)
    system._get_next_due_datetime = lambda raw_slots, now=None: (fixed_due, "20:00")

    system._process_secured_bottle_placements()

    secure_state = system.secured_medications["station_1"]
    assert secure_state["medicine_id"] == "M001"
    assert secure_state["scheduled_time"] == "20:00"
    assert secure_state["present"] is True
    assert secure_state["authorized"] is False
    assert secure_state["secured_weight_g"] == 42.5


def test_unauthorized_bottle_movement_alerts_once_before_due():
    system = make_system()
    system.weight_manager = FakeWeightManager({
        "connected": True,
        "stable": True,
        "weight_g": 0.0,
        "event_detection_enabled": False,
    })
    system.secured_medications["station_1"] = {
        "medicine_name": "Aspirin 100mg",
        "station_id": "station_1",
        "next_due_timestamp": time.time() + 3600,
        "next_due_display": "2026-03-25 20:00:00",
        "present": True,
        "authorized": False,
        "early_alert_sent": False,
    }

    system._process_secured_bottle_movements()
    system._process_secured_bottle_movements()

    assert len(system.telegram.alerts) == 1
    assert system.secured_medications["station_1"]["early_alert_sent"] is True


def test_authorize_current_medication_captures_baseline_before_enabling_detection():
    system = make_system()
    system.current_medication = {
        "medicine_name": "Aspirin 100mg",
        "station_id": "station_1",
        "dosage_pills": 2,
    }
    system.weight_manager = FakeWeightManager({
        "connected": True,
        "stable": True,
        "weight_g": 55.0,
        "event_detection_enabled": False,
    })
    system.state_machine = FakeStateMachine(SystemState.REMINDER_ACTIVE)
    system.secured_medications["station_1"] = {
        "authorized": False,
    }

    ok = system._authorize_current_medication_if_ready()

    assert ok is True
    assert system.weight_manager.calls == [
        ("capture", "station_1"),
        ("enable", "station_1"),
    ]
    assert system.secured_medications["station_1"]["authorized"] is True
    assert system.secured_medications["station_1"]["authorized_baseline_g"] == 55.0


def test_weight_manager_rolls_baseline_to_returned_weight(tmp_path):
    logger = logging.getLogger("scheduled_bottle_guard_test")
    config = {
        "station_1": {
            "id": "station_1",
            "pill_weight_mg": 500,
            "event_settle_seconds": 0.5,
            "event_cooldown_seconds": 0.0,
            "min_delta_g": 0.2,
            "dose_verification_tolerance_g": 0.12,
        }
    }
    manager = WeightManager(config, logger)
    manager.baseline_file = tmp_path / "station_baselines.json"
    manager.baseline_file.parent.mkdir(parents=True, exist_ok=True)

    manager.process_weight_data({
        "station_id": "station_1",
        "weight_g": 10.0,
        "stable": True,
        "received_at": 1.0,
    })
    assert manager.capture_current_baseline("station_1") is True

    manager.enable_event_detection("station_1")
    manager.process_weight_data({
        "station_id": "station_1",
        "weight_g": 0.0,
        "stable": True,
        "received_at": 2.0,
    })
    manager.process_weight_data({
        "station_id": "station_1",
        "weight_g": 9.0,
        "stable": True,
        "received_at": 2.1,
    })
    manager.process_weight_data({
        "station_id": "station_1",
        "weight_g": 9.0,
        "stable": True,
        "received_at": 2.8,
    })

    event = manager.last_event_data["station_1"]
    assert event["previous_baseline_g"] == 10.0
    assert event["current_weight_g"] == 9.0
    assert manager.baseline_weights["station_1"] == 9.0
    assert manager.verify_dosage("station_1", expected_pills=2)["verified"] is True
