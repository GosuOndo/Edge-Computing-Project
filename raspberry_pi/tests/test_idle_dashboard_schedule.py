import sys
import types
from datetime import datetime


schedule_stub = types.ModuleType("schedule")
schedule_stub.every = lambda: None
schedule_stub.run_pending = lambda: None
schedule_stub.get_jobs = lambda: []
schedule_stub.clear = lambda: None
sys.modules.setdefault("schedule", schedule_stub)

from raspberry_pi.services.scheduler import MedicationScheduler


class DummyLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def make_scheduler():
    config = {
        "medications": [
            {
                "name": "Aspirin 100mg",
                "station_id": "station_1",
                "dosage_pills": 1,
                "times": ["08:00", "20:00"],
            },
            {
                "name": "Metformin 500mg",
                "station_id": "station_2",
                "dosage_pills": 2,
                "times": ["09:30", "21:00"],
            },
        ],
        "reminder": {
            "advance_minutes": 5,
            "timeout_minutes": 30,
        },
    }
    return MedicationScheduler(config, DummyLogger())


def test_get_next_scheduled_time_returns_real_medicine(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 3, 26, 8, 15, 0)

    monkeypatch.setattr("raspberry_pi.services.scheduler.datetime", FixedDateTime)

    scheduler = make_scheduler()
    next_item = scheduler.get_next_scheduled_time()

    assert next_item["medicine_name"] == "Metformin 500mg"
    assert next_item["station_id"] == "station_2"
    assert next_item["time"] == "09:30"


def test_get_todays_schedule_returns_sorted_timetable():
    scheduler = make_scheduler()

    timetable = scheduler.get_todays_schedule()

    assert [item["time"] for item in timetable] == ["08:00", "09:30", "20:00", "21:00"]
    assert timetable[0]["medicine_name"] == "Aspirin 100mg"
    assert timetable[1]["medicine_name"] == "Metformin 500mg"


def test_scheduler_defaults_when_reminder_block_is_missing():
    config = {
        "medications": [
            {
                "name": "Aspirin 100mg",
                "station_id": "station_1",
                "dosage_pills": 1,
                "times": ["08:00"],
            }
        ]
    }

    scheduler = MedicationScheduler(config, DummyLogger())

    assert scheduler.reminder_advance_minutes == 5
    assert scheduler.timeout_minutes == 30
    assert scheduler.get_scheduled_medicines() == ["Aspirin 100mg"]
