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
    def __init__(self, record=None, records_by_tag=None, records_by_station=None):
        self.record = record
        self.records_by_tag = records_by_tag or {}
        self.records_by_station = records_by_station or {}

    def get_registered_medicine_by_tag_uid(self, tag_uid):
        return self.records_by_tag.get(tag_uid, self.record)

    def get_registered_medicine_by_station(self, station_id):
        if self.records_by_station:
            return self.records_by_station.get(station_id)
        return self.record

    def list_registered_medicines(self):
        if self.records_by_station:
            return [r for r in self.records_by_station.values() if r]
        return [self.record] if self.record else []


class FakeTagManager:
    def __init__(self, record=None):
        self.record = record

    def build_record_from_scan(self, scan_msg):
        return self.record


class FakeTagRuntimeService:
    def __init__(self, latest=None, latest_by_station=None, record=None):
        self.latest = latest
        self.latest_by_station = latest_by_station or {}
        self.tag_manager = FakeTagManager(record)
        self.scan_commands = []
        self.cleared_stations = []

    def get_latest_scan(self, station_id=None):
        if station_id is None:
            if self.latest is not None:
                return self.latest
            if not self.latest_by_station:
                return None
            return max(
                self.latest_by_station.values(),
                key=lambda entry: entry.get("received_at", 0.0),
            )
        if self.latest_by_station:
            return self.latest_by_station.get(station_id)
        return self.latest

    def start_scanning(self, station_id=None):
        self.scan_commands.append(("start", station_id))

    def stop_scanning(self, station_id=None):
        self.scan_commands.append(("stop", station_id))

    def clear_latest_scan(self, station_id=None):
        self.cleared_stations.append(station_id)
        if station_id is None:
            self.latest = None
            self.latest_by_station.clear()
        else:
            self.latest_by_station.pop(station_id, None)
            if self.latest == self.latest_by_station.get(station_id):
                self.latest = None


class FakeWeightManager:
    def __init__(self, status):
        if (
            isinstance(status, dict)
            and status
            and all(isinstance(value, dict) for value in status.values())
        ):
            self.status_by_station = {
                station_id: dict(station_status)
                for station_id, station_status in status.items()
            }
        else:
            self.status_by_station = {"station_1": dict(status)}

        self.calls = []
        self.baseline_weights = {
            station_id: station_status.get("weight_g", 0.0)
            for station_id, station_status in self.status_by_station.items()
        }
        self.baseline_capture_required = {
            station_id: True for station_id in self.status_by_station
        }
        self.station_configs = {
            station_id: {"id": station_id}
            for station_id in self.status_by_station
        }

    def get_station_status(self, station_id):
        return dict(self.status_by_station[station_id])

    def capture_current_baseline(self, station_id):
        self.calls.append(("capture", station_id))
        self.baseline_weights[station_id] = self.status_by_station[station_id].get(
            "weight_g", 0.0
        )
        self.baseline_capture_required[station_id] = False
        return True

    def enable_event_detection(self, station_id):
        self.calls.append(("enable", station_id))
        self.status_by_station[station_id]["event_detection_enabled"] = True

    def disable_event_detection(self, station_id):
        self.calls.append(("disable", station_id))
        self.status_by_station[station_id]["event_detection_enabled"] = False


class FakeStateMachine:
    def __init__(self, state):
        self.state = state

    def get_state(self):
        return self.state

    def reset_to_idle(self):
        self.state = SystemState.IDLE


class FakeTelegram:
    def __init__(self):
        self.alerts = []

    def send_unauthorized_bottle_movement_alert(self, **kwargs):
        self.alerts.append(kwargs)
        return True


class FakeDisplay:
    def __init__(self):
        self.warning_calls = []
        self.error_calls = []
        self.idle_calls = []
        self.pipeline_calls = []
        self.overdose_calls = []
        self.success_calls = []

    def show_warning_screen(self, title, message):
        self.warning_calls.append((title, message))

    def show_error_screen(self, message):
        self.error_calls.append(message)

    def show_idle_screen(self, next_scheduled=None):
        self.idle_calls.append(next_scheduled)

    def show_pipeline_screen(self, title, message):
        self.pipeline_calls.append((title, message))

    def show_overdose_screen(self, medicine_name, taken, required):
        self.overdose_calls.append((medicine_name, taken, required))

    def show_success_screen(self, medicine_name, message):
        self.success_calls.append((medicine_name, message))

    def show_security_alert_screen(self, issues):
        label_map = {
            "missing": "missing bottle",
            "incorrect": "wrong bottle",
            "tampered": "tampered bottle",
        }
        summary = " | ".join(
            f"{issue.get('station_label')} {label_map.get(issue.get('issue'), issue.get('issue'))}"
            for issue in issues
        )
        self.error_calls.append(summary)


class FakeAudio:
    def __init__(self):
        self.messages = []
        self.clear_requests = []

    def speak_async(self, message):
        self.messages.append(message)

    def announce_warning(self, message):
        self.messages.append(message)

    def clear_pending(self, text_contains=None):
        self.clear_requests.append(text_contains)


def make_system():
    system = MedicationSystem.__new__(MedicationSystem)
    system.logger = DummyLogger()
    system.current_medication = None
    system._firmware_dosing_active = False
    system.secured_medications = {}
    system._processed_tag_scans = {}
    system._last_station_scan_audit = {}
    system.min_secured_bottle_weight_g = 5.0
    system._last_security_violation_message = None
    system.telegram = FakeTelegram()
    system.display = None
    system.audio = None
    system.tag_runtime_service = FakeTagRuntimeService()
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


def test_bootstrap_from_registration_marks_missing_bottles_and_shows_alert():
    record_1 = {
        "medicine_id": "M001",
        "medicine_name": "Aspirin 100mg",
        "station_id": "station_1",
        "tag_uid": "TAG123",
        "time_slots": "08:00,20:00",
    }
    record_2 = {
        "medicine_id": "M002",
        "medicine_name": "Metformin 500mg",
        "station_id": "station_2",
        "tag_uid": "TAG456",
        "time_slots": "09:00,21:00",
    }

    system = make_system()
    system.display = FakeDisplay()
    system.database = FakeDatabase(
        records_by_station={"station_1": record_1, "station_2": record_2}
    )
    system.weight_manager = FakeWeightManager({
        "station_1": {
            "connected": True,
            "stable": True,
            "weight_g": 0.0,
            "event_detection_enabled": False,
        },
        "station_2": {
            "connected": True,
            "stable": True,
            "weight_g": 0.0,
            "event_detection_enabled": False,
        },
    })
    due_map = {
        "08:00,20:00": (datetime(2099, 3, 25, 20, 0, 0), "20:00"),
        "09:00,21:00": (datetime(2099, 3, 25, 21, 0, 0), "21:00"),
    }
    system._get_next_due_datetime = lambda raw_slots, now=None: due_map[raw_slots]

    system._bootstrap_registered_station_security_state()
    system._refresh_security_violation_screen()

    assert system.secured_medications["station_1"]["early_alert_sent"] is True
    assert system.secured_medications["station_1"]["present"] is False
    assert system.secured_medications["station_2"]["early_alert_sent"] is True
    assert system.secured_medications["station_2"]["present"] is False
    assert system.display.error_calls[-1] == (
        "Station 1 missing bottle | Station 2 missing bottle"
    )


def test_station_with_scheduler_times_skips_onboarding():
    system = make_system()
    system.database = FakeDatabase(records_by_station={"station_1": None})
    system.scheduler = types.SimpleNamespace(
        medications=[
            {
                "name": "Aspirin 100mg",
                "station_id": "station_1",
                "dosage_pills": 1,
                "times": ["08:00"],
            }
        ]
    )

    assert system._station_has_existing_schedule("station_1") is True


def test_end_verification_cycle_keeps_continuous_scanning_active():
    system = make_system()
    system.state_machine = FakeStateMachine(SystemState.MONITORING_PATIENT)
    system.weight_manager = FakeWeightManager({
        "station_1": {
            "connected": True,
            "stable": True,
            "weight_g": 42.5,
            "event_detection_enabled": True,
        }
    })
    system.display = FakeDisplay()
    system.current_medication = {"station_id": "station_1"}
    system.pending_monitoring_ui = (0, 30, "Monitoring...", 0, 1)
    system.secured_medications = {"station_1": {"medicine_name": "Aspirin 100mg"}}
    system._dose_pills_removed = {"station_1": 1}
    system._dose_attempt_count = {"station_1": 1}

    system._end_verification_cycle("station_1")

    assert ("disable", "station_1") in system.weight_manager.calls
    assert ("start", None) in system.tag_runtime_service.scan_commands
    assert system.current_medication is None
    assert system.secured_medications == {}
    assert system.display.idle_calls != []


def test_nfc_audit_retriggers_scan_for_occupied_station_after_relaunch():
    system = make_system()
    system.weight_manager = FakeWeightManager({
        "station_1": {
            "connected": True,
            "stable": True,
            "weight_g": 42.5,
            "event_detection_enabled": False,
        }
    })
    system._last_station_scan_audit = {}
    system._processed_tag_scans = {"station_1": 100.0}
    system.tag_runtime_service = FakeTagRuntimeService(
        latest_by_station={
            "station_1": {
                "received_at": 100.0,
                "scan_msg": {"tag_uid": "TAG123"},
            }
        }
    )

    system._audit_occupied_stations_with_nfc(audit_interval_seconds=0.0)

    assert "station_1" in system.tag_runtime_service.cleared_stations
    assert ("start", "station_1") in system.tag_runtime_service.scan_commands


def test_monitoring_overdose_returns_to_idle_without_success(monkeypatch):
    monkeypatch.setattr("raspberry_pi.main.time.sleep", lambda *_args, **_kwargs: None)

    system = make_system()
    system.running = True
    system.config = {
        "identity": {
            "tag": {
                "integrated_mode": True,
                "coincident_window_seconds": 15.0,
            }
        }
    }
    system.current_medication = {
        "medicine_name": "Aspirin 100mg",
        "dosage_pills": 1,
        "station_id": "station_1",
        "medicine_id": "M001",
    }
    system._dose_pills_removed = {"station_1": 1}
    system.display = FakeDisplay()
    system.audio = FakeAudio()
    system.telegram = types.SimpleNamespace(
        alerts=[],
        send_incorrect_dosage_alert=lambda **kwargs: system.telegram.alerts.append(kwargs) or True,
    )
    system.database = types.SimpleNamespace(
        logged=[],
        log_medication_event=lambda decision: system.database.logged.append(decision) or True,
    )
    system.scanner = types.SimpleNamespace(
        initialize_camera=lambda: True,
        release_camera=lambda: None,
    )
    system.identity_manager = types.SimpleNamespace(
        verify_identity_integrated=lambda **_kwargs: {
            "success": True,
            "medicine_name": "Aspirin 100mg",
            "confidence": 1.0,
            "method": "tag",
            "record": {},
        }
    )
    system.weight_manager = types.SimpleNamespace(
        verify_dosage=lambda station_id, expected_dosage: {
            "verified": True,
            "actual": expected_dosage,
            "pill_weight_g": 0.5,
        },
        _get_pill_weight_g=lambda station_id: 0.5,
        station_configs={"station_1": {"dose_verification_tolerance_g": 0.12}},
        set_pill_weight_from_tag=lambda station_id, pill_weight_mg: None,
    )
    system.decision_engine = types.SimpleNamespace(
        verify_medication_intake=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("decision engine should not run after intake overdose")
        )
    )
    system.scheduler = types.SimpleNamespace(
        mark_dose_taken=lambda medicine_name: (_ for _ in ()).throw(
            AssertionError("dose should not be marked taken after intake overdose")
        )
    )
    system._handle_decision = lambda decision: (_ for _ in ()).throw(
        AssertionError("success/warning handler should not run after intake overdose")
    )
    ended_cycles = []
    system._end_verification_cycle = lambda station_id: ended_cycles.append(station_id)
    system._run_monitoring_session = lambda expected_dosage: {
        "compliance_status": "good",
        "swallow_count": 2,
        "cough_count": 0,
        "hand_motion_count": 0,
    }

    system._verify_medication_intake({"timestamp": time.time()})

    assert system.display.overdose_calls == [("Aspirin 100mg", 2, 1)]
    assert system.display.success_calls == []
    assert system.telegram.alerts == [
        {
            "medicine_name": "Aspirin 100mg",
            "expected": 1,
            "actual": 2,
        }
    ]
    assert len(system.database.logged) == 1
    assert system.database.logged[0]["result"] == StubDecisionResult.INCORRECT_DOSAGE
    assert system.database.logged[0]["details"]["weight_actual"] == 2
    assert system.database.logged[0]["details"]["dose_error_stage"] == "intake_monitoring"
    assert ended_cycles == ["station_1"]


def test_process_secured_bottle_placements_secures_both_registered_stations():
    record_1 = {
        "medicine_id": "M001",
        "medicine_name": "Aspirin 100mg",
        "station_id": "station_1",
        "tag_uid": "TAG123",
        "time_slots": "08:00,20:00",
    }
    record_2 = {
        "medicine_id": "M002",
        "medicine_name": "Metformin 500mg",
        "station_id": "station_2",
        "tag_uid": "TAG456",
        "time_slots": "09:00,21:00",
    }

    system = make_system()
    system.database = FakeDatabase(
        records_by_tag={"TAG123": record_1, "TAG456": record_2},
        records_by_station={"station_1": record_1, "station_2": record_2},
    )
    system.tag_runtime_service = FakeTagRuntimeService(
        latest_by_station={
            "station_1": {
                "received_at": 123.0,
                "scan_msg": {"tag_uid": "TAG123"},
            },
            "station_2": {
                "received_at": 456.0,
                "scan_msg": {"tag_uid": "TAG456"},
            },
        }
    )
    system.weight_manager = FakeWeightManager({
        "station_1": {
            "connected": True,
            "stable": True,
            "weight_g": 42.5,
            "event_detection_enabled": False,
        },
        "station_2": {
            "connected": True,
            "stable": True,
            "weight_g": 51.0,
            "event_detection_enabled": False,
        },
    })
    system._get_next_due_datetime = (
        lambda raw_slots, now=None: (datetime(2026, 3, 25, 20, 0, 0), "20:00")
    )

    system._process_secured_bottle_placements()

    assert set(system.secured_medications) == {"station_1", "station_2"}
    assert system.secured_medications["station_1"]["medicine_id"] == "M001"
    assert system.secured_medications["station_2"]["medicine_id"] == "M002"
    assert system.secured_medications["station_1"]["secured_weight_g"] == 42.5
    assert system.secured_medications["station_2"]["secured_weight_g"] == 51.0


def test_wrong_cross_station_bottle_on_station_2_uses_security_alert_screen():
    record_1 = {
        "medicine_id": "M001",
        "medicine_name": "Aspirin 100mg",
        "station_id": "station_1",
        "tag_uid": "TAG123",
        "time_slots": "08:00,20:00",
    }
    record_2 = {
        "medicine_id": "M002",
        "medicine_name": "Metformin 500mg",
        "station_id": "station_2",
        "tag_uid": "TAG456",
        "time_slots": "09:00,21:00",
    }

    system = make_system()
    system.display = FakeDisplay()
    system.audio = FakeAudio()
    system.database = FakeDatabase(
        records_by_tag={"TAG123": record_1, "TAG456": record_2},
        records_by_station={"station_1": record_1, "station_2": record_2},
    )
    system.tag_runtime_service = FakeTagRuntimeService(
        latest_by_station={
            "station_2": {
                "received_at": 123.0,
                "scan_msg": {"tag_uid": "TAG123"},
            }
        }
    )
    system.weight_manager = FakeWeightManager({
        "station_1": {
            "connected": True,
            "stable": True,
            "weight_g": 42.5,
            "event_detection_enabled": False,
        },
        "station_2": {
            "connected": True,
            "stable": True,
            "weight_g": 51.0,
            "event_detection_enabled": False,
        },
    })
    future_ts = time.time() + 3600
    system.secured_medications = {
        "station_2": {
            "medicine_id": "M002",
            "medicine_name": "Metformin 500mg",
            "station_id": "station_2",
            "tag_uid": "TAG456",
            "next_due_timestamp": future_ts,
            "next_due_display": "2026-03-25 21:00:00",
            "scheduled_time": "21:00",
            "present": True,
            "authorized": False,
            "early_alert_sent": False,
        }
    }

    system._process_secured_bottle_placements()

    assert system.secured_medications["station_2"]["wrong_bottle_on_station"] is True
    assert system.display.error_calls == ["Station 2 wrong bottle"]
    assert system.display.warning_calls == []
    assert system.audio.messages == [
        "Wrong medicine detected. Please place Metformin 500mg on station_2"
    ]


def test_wrong_medicine_audio_is_not_requeued_and_is_cleared_after_correction():
    record_1 = {
        "medicine_id": "M001",
        "medicine_name": "Aspirin 100mg",
        "station_id": "station_1",
        "tag_uid": "TAG123",
        "time_slots": "08:00,20:00",
    }
    wrong_record = {
        "medicine_id": "M999",
        "medicine_name": "Vitamin C",
        "station_id": "station_2",
        "tag_uid": "WRONG999",
        "time_slots": "09:00",
    }

    system = make_system()
    system.display = FakeDisplay()
    system.audio = FakeAudio()
    system.database = FakeDatabase(
        records_by_tag={"TAG123": record_1, "WRONG999": wrong_record},
        records_by_station={"station_1": record_1},
    )
    system.tag_runtime_service = FakeTagRuntimeService(
        latest_by_station={
            "station_1": {
                "received_at": 100.0,
                "scan_msg": {"tag_uid": "WRONG999"},
            }
        }
    )
    system.weight_manager = FakeWeightManager({
        "station_1": {
            "connected": True,
            "stable": True,
            "weight_g": 42.5,
            "event_detection_enabled": False,
        }
    })
    future_ts = time.time() + 3600
    system.secured_medications = {
        "station_1": {
            "medicine_id": "M001",
            "medicine_name": "Aspirin 100mg",
            "station_id": "station_1",
            "tag_uid": "TAG123",
            "next_due_timestamp": future_ts,
            "next_due_display": "2026-03-25 20:00:00",
            "scheduled_time": "20:00",
            "present": True,
            "authorized": False,
            "early_alert_sent": False,
        }
    }
    system._get_next_due_datetime = (
        lambda raw_slots, now=None: (datetime(2026, 3, 25, 20, 0, 0), "20:00")
    )

    system._process_secured_bottle_placements()
    system._process_secured_bottle_placements()

    system.tag_runtime_service.latest_by_station["station_1"] = {
        "received_at": 101.0,
        "scan_msg": {"tag_uid": "TAG123"},
    }

    system._process_secured_bottle_placements()

    assert system.audio.messages == [
        "Wrong medicine detected. Please place Aspirin 100mg on station_1"
    ]
    assert system.audio.clear_requests == [
        ("Wrong medicine detected", "Wrong bottle detected")
    ]
    assert system.display.error_calls == ["Station 1 wrong bottle"]
    assert system.display.idle_calls != []
    assert "wrong_bottle_on_station" not in system.secured_medications["station_1"]


def test_station_1_missing_detection_still_works_after_wrong_bottle_is_corrected():
    record_1 = {
        "medicine_id": "M001",
        "medicine_name": "Aspirin 100mg",
        "station_id": "station_1",
        "tag_uid": "TAG123",
        "time_slots": "08:00,20:00",
    }
    wrong_record = {
        "medicine_id": "M999",
        "medicine_name": "Vitamin C",
        "station_id": "station_2",
        "tag_uid": "WRONG999",
        "time_slots": "09:00",
    }

    system = make_system()
    system.display = FakeDisplay()
    system.audio = FakeAudio()
    system.database = FakeDatabase(
        records_by_tag={"TAG123": record_1, "WRONG999": wrong_record},
        records_by_station={"station_1": record_1},
    )
    system.tag_runtime_service = FakeTagRuntimeService(
        latest_by_station={
            "station_1": {
                "received_at": 100.0,
                "scan_msg": {"tag_uid": "WRONG999"},
            }
        }
    )
    system.weight_manager = FakeWeightManager({
        "station_1": {
            "connected": True,
            "stable": True,
            "weight_g": 42.5,
            "event_detection_enabled": False,
        }
    })
    future_ts = time.time() + 3600
    system.secured_medications = {
        "station_1": {
            "medicine_id": "M001",
            "medicine_name": "Aspirin 100mg",
            "station_id": "station_1",
            "tag_uid": "TAG123",
            "next_due_timestamp": future_ts,
            "next_due_display": "2026-03-25 20:00:00",
            "scheduled_time": "20:00",
            "present": True,
            "authorized": False,
            "early_alert_sent": False,
        }
    }
    # Simulate a scan payload whose schedule would otherwise resolve badly;
    # the existing future due window should be preserved.
    system._get_next_due_datetime = (
        lambda raw_slots, now=None: (datetime(2026, 3, 25, 20, 0, 0), "20:00")
    )

    system._process_secured_bottle_placements()
    system.tag_runtime_service.latest_by_station["station_1"] = {
        "received_at": 101.0,
        "scan_msg": {"tag_uid": "TAG123"},
    }
    system._process_secured_bottle_placements()

    system.weight_manager.status_by_station["station_1"]["weight_g"] = 0.0
    system._process_secured_bottle_movements()

    assert system.secured_medications["station_1"]["early_alert_sent"] is True
    assert system.secured_medications["station_1"]["present"] is False
    assert system.display.error_calls[-1] == "Station 1 missing bottle"


def test_unauthorized_bottle_movement_alerts_once_before_due():
    system = make_system()
    system.display = FakeDisplay()
    system.audio = FakeAudio()
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
    assert system.display.error_calls == [
        "Station 1 missing bottle"
    ]
    assert system.audio.messages == [
        "Aspirin 100mg removed from station_1. Place it back on the correct station."
    ]


def test_simultaneous_missing_bottles_show_combined_error_screen():
    system = make_system()
    system.display = FakeDisplay()
    system.weight_manager = FakeWeightManager({
        "station_1": {
            "connected": True,
            "stable": True,
            "weight_g": 0.0,
            "event_detection_enabled": False,
        },
        "station_2": {
            "connected": True,
            "stable": True,
            "weight_g": 0.0,
            "event_detection_enabled": False,
        },
    })
    future_ts = time.time() + 3600
    system.secured_medications = {
        "station_1": {
            "medicine_name": "Aspirin 100mg",
            "station_id": "station_1",
            "next_due_timestamp": future_ts,
            "next_due_display": "2026-03-25 20:00:00",
            "present": True,
            "authorized": False,
            "early_alert_sent": False,
        },
        "station_2": {
            "medicine_name": "Metformin 500mg",
            "station_id": "station_2",
            "next_due_timestamp": future_ts,
            "next_due_display": "2026-03-25 21:00:00",
            "present": True,
            "authorized": False,
            "early_alert_sent": False,
        },
    }

    system._process_secured_bottle_movements()

    assert system.display.error_calls[-1] == (
        "Station 1 missing bottle | Station 2 missing bottle"
    )


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


def test_returned_bottle_waits_for_fresh_station_scan():
    system = make_system()
    system.display = FakeDisplay()
    system.weight_manager = FakeWeightManager({
        "station_1": {
            "connected": True,
            "stable": True,
            "weight_g": 55.0,
            "event_detection_enabled": False,
        }
    })
    system.tag_runtime_service = FakeTagRuntimeService(
        latest_by_station={
            "station_1": {
                "received_at": 100.0,
                "scan_msg": {"tag_uid": "OLD"},
            }
        }
    )
    now_ts = time.time()
    system.secured_medications["station_1"] = {
        "medicine_id": "M001",
        "medicine_name": "Aspirin 100mg",
        "station_id": "station_1",
        "tag_uid": "TAG123",
        "next_due_timestamp": now_ts + 3600,
        "next_due_display": "2026-03-25 20:00:00",
        "present": False,
        "authorized": False,
        "early_alert_sent": True,
        "early_alert_sent_at": now_ts - 5,
        "bottle_returned_at": now_ts - 3,
    }

    system._process_secured_bottle_movements()

    secure_state = system.secured_medications["station_1"]
    assert secure_state["early_alert_sent"] is True
    assert secure_state["present"] is False
    assert secure_state["bottle_returned_at"] <= now_ts - 3
    assert system.tag_runtime_service.scan_commands == []


def test_simultaneous_returns_are_verified_per_station():
    record_1 = {
        "medicine_id": "M001",
        "medicine_name": "Aspirin 100mg",
        "station_id": "station_1",
        "tag_uid": "TAG123",
        "time_slots": "08:00,20:00",
    }
    record_2 = {
        "medicine_id": "M002",
        "medicine_name": "Metformin 500mg",
        "station_id": "station_2",
        "tag_uid": "TAG456",
        "time_slots": "09:00,21:00",
    }
    wrong_record = {
        "medicine_id": "M999",
        "medicine_name": "Vitamin C",
        "station_id": "station_2",
        "tag_uid": "WRONG999",
        "time_slots": "09:00",
    }

    system = make_system()
    system.display = FakeDisplay()
    system.audio = FakeAudio()
    system.database = FakeDatabase(
        records_by_tag={
            "TAG123": record_1,
            "TAG456": record_2,
            "WRONG999": wrong_record,
        },
        records_by_station={"station_1": record_1, "station_2": record_2},
    )
    now_ts = time.time()
    system.tag_runtime_service = FakeTagRuntimeService(
        latest_by_station={
            "station_1": {
                "received_at": now_ts - 1.0,
                "scan_msg": {"tag_uid": "TAG123"},
            },
            "station_2": {
                "received_at": now_ts - 0.5,
                "scan_msg": {"tag_uid": "WRONG999"},
            },
        }
    )
    system.weight_manager = FakeWeightManager({
        "station_1": {
            "connected": True,
            "stable": True,
            "weight_g": 43.0,
            "event_detection_enabled": False,
        },
        "station_2": {
            "connected": True,
            "stable": True,
            "weight_g": 48.0,
            "event_detection_enabled": False,
        },
    })
    system.secured_medications = {
        "station_1": {
            "medicine_id": "M001",
            "medicine_name": "Aspirin 100mg",
            "station_id": "station_1",
            "tag_uid": "TAG123",
            "next_due_timestamp": now_ts + 3600,
            "next_due_display": "2026-03-25 20:00:00",
            "present": False,
            "authorized": False,
            "early_alert_sent": True,
            "early_alert_sent_at": now_ts - 10,
            "bottle_returned_at": now_ts - 3,
        },
        "station_2": {
            "medicine_id": "M002",
            "medicine_name": "Metformin 500mg",
            "station_id": "station_2",
            "tag_uid": "TAG456",
            "next_due_timestamp": now_ts + 3600,
            "next_due_display": "2026-03-25 21:00:00",
            "present": False,
            "authorized": False,
            "early_alert_sent": True,
            "early_alert_sent_at": now_ts - 10,
            "bottle_returned_at": now_ts - 3,
        },
    }

    system._process_secured_bottle_movements()

    station_1 = system.secured_medications["station_1"]
    station_2 = system.secured_medications["station_2"]

    assert station_1["present"] is True
    assert station_1["early_alert_sent"] is False
    assert station_2["present"] is False
    assert station_2["early_alert_sent"] is True
    assert station_2["wrong_bottle_on_station"] is True
    assert system.display.idle_calls == []
    assert system.display.error_calls[-1] == (
        "Station 2 wrong bottle"
    )
    assert ("start", "station_1") in system.tag_runtime_service.scan_commands
    assert ("start", "station_2") in system.tag_runtime_service.scan_commands
    assert "station_2" in system.tag_runtime_service.cleared_stations


def test_one_correct_one_missing_keeps_remaining_station_error_on_screen():
    record_1 = {
        "medicine_id": "M001",
        "medicine_name": "Aspirin 100mg",
        "station_id": "station_1",
        "tag_uid": "TAG123",
        "time_slots": "08:00,20:00",
    }

    system = make_system()
    system.display = FakeDisplay()
    system.database = FakeDatabase(
        records_by_tag={"TAG123": record_1},
        records_by_station={"station_1": record_1},
    )
    now_ts = time.time()
    system.tag_runtime_service = FakeTagRuntimeService(
        latest_by_station={
            "station_1": {
                "received_at": now_ts - 1.0,
                "scan_msg": {"tag_uid": "TAG123"},
            }
        }
    )
    system.weight_manager = FakeWeightManager({
        "station_1": {
            "connected": True,
            "stable": True,
            "weight_g": 43.0,
            "event_detection_enabled": False,
        },
        "station_2": {
            "connected": True,
            "stable": True,
            "weight_g": 0.0,
            "event_detection_enabled": False,
        },
    })
    system.secured_medications = {
        "station_1": {
            "medicine_id": "M001",
            "medicine_name": "Aspirin 100mg",
            "station_id": "station_1",
            "tag_uid": "TAG123",
            "next_due_timestamp": now_ts + 3600,
            "next_due_display": "2026-03-25 20:00:00",
            "present": False,
            "authorized": False,
            "early_alert_sent": True,
            "early_alert_sent_at": now_ts - 10,
            "bottle_returned_at": now_ts - 3,
        },
        "station_2": {
            "medicine_id": "M002",
            "medicine_name": "Metformin 500mg",
            "station_id": "station_2",
            "tag_uid": "TAG456",
            "next_due_timestamp": now_ts + 3600,
            "next_due_display": "2026-03-25 21:00:00",
            "present": False,
            "authorized": False,
            "early_alert_sent": True,
            "early_alert_sent_at": now_ts - 10,
        },
    }

    system._process_secured_bottle_movements()

    assert system.secured_medications["station_1"]["early_alert_sent"] is False
    assert system.secured_medications["station_2"]["early_alert_sent"] is True
    assert system.display.error_calls[-1] == "Station 2 missing bottle"


def test_both_incorrect_bottles_show_combined_error_screen():
    wrong_record_1 = {
        "medicine_id": "M900",
        "medicine_name": "Vitamin C",
        "station_id": "station_1",
        "tag_uid": "WRONG1",
        "time_slots": "08:00",
    }
    wrong_record_2 = {
        "medicine_id": "M901",
        "medicine_name": "Fish Oil",
        "station_id": "station_2",
        "tag_uid": "WRONG2",
        "time_slots": "09:00",
    }

    system = make_system()
    system.display = FakeDisplay()
    system.audio = FakeAudio()
    now_ts = time.time()
    system.database = FakeDatabase(
        records_by_tag={
            "WRONG1": wrong_record_1,
            "WRONG2": wrong_record_2,
        }
    )
    system.tag_runtime_service = FakeTagRuntimeService(
        latest_by_station={
            "station_1": {
                "received_at": now_ts - 1.0,
                "scan_msg": {"tag_uid": "WRONG1"},
            },
            "station_2": {
                "received_at": now_ts - 0.5,
                "scan_msg": {"tag_uid": "WRONG2"},
            },
        }
    )
    system.weight_manager = FakeWeightManager({
        "station_1": {
            "connected": True,
            "stable": True,
            "weight_g": 40.0,
            "event_detection_enabled": False,
        },
        "station_2": {
            "connected": True,
            "stable": True,
            "weight_g": 41.0,
            "event_detection_enabled": False,
        },
    })
    system.secured_medications = {
        "station_1": {
            "medicine_id": "M001",
            "medicine_name": "Aspirin 100mg",
            "station_id": "station_1",
            "tag_uid": "TAG123",
            "next_due_timestamp": now_ts + 3600,
            "next_due_display": "2026-03-25 20:00:00",
            "present": False,
            "authorized": False,
            "early_alert_sent": True,
            "early_alert_sent_at": now_ts - 10,
            "bottle_returned_at": now_ts - 3,
        },
        "station_2": {
            "medicine_id": "M002",
            "medicine_name": "Metformin 500mg",
            "station_id": "station_2",
            "tag_uid": "TAG456",
            "next_due_timestamp": now_ts + 3600,
            "next_due_display": "2026-03-25 21:00:00",
            "present": False,
            "authorized": False,
            "early_alert_sent": True,
            "early_alert_sent_at": now_ts - 10,
            "bottle_returned_at": now_ts - 3,
        },
    }

    system._process_secured_bottle_movements()

    assert system.secured_medications["station_1"]["wrong_bottle_on_station"] is True
    assert system.secured_medications["station_2"]["wrong_bottle_on_station"] is True
    assert system.display.error_calls[-1] == (
        "Station 1 wrong bottle | Station 2 wrong bottle"
    )


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
