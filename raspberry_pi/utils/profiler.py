"""
PASO profiling helpers for edge-device instrumentation.
"""

import csv
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Optional

try:
    import psutil
except ImportError:  # pragma: no cover - optional on some targets
    psutil = None


class PASOProfiler:
    """Lightweight CSV profiler for PASO deployment measurements."""

    HEADERS = [
        "run_id",
        "scenario",
        "station_id",
        "stage",
        "start_ts",
        "end_ts",
        "duration_ms",
        "cpu_percent",
        "memory_mb",
        "temperature_c",
        "notes",
    ]

    def __init__(self, output_path: str = "data/paso_metrics.csv"):
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._process = psutil.Process(os.getpid()) if psutil else None

        if not self.output_path.exists():
            with self.output_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(self.HEADERS)

    def log_stage(
        self,
        run_id: str,
        scenario: str,
        station_id: str,
        stage: str,
        start_ts: float,
        end_ts: float,
        notes: Any = "",
        duration_ms: Optional[float] = None,
    ):
        if duration_ms is None:
            duration_ms = max(0.0, (float(end_ts) - float(start_ts)) * 1000.0)

        cpu_percent = self._get_cpu_percent()
        memory_mb = self._get_memory_mb()
        temperature_c = self._get_temperature_c()

        row = [
            run_id,
            scenario,
            station_id,
            stage,
            round(float(start_ts), 6),
            round(float(end_ts), 6),
            round(float(duration_ms), 3),
            cpu_percent,
            memory_mb,
            temperature_c,
            self._serialise_notes(notes),
        ]

        with self._lock:
            with self.output_path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(row)

    def log_stage_window(
        self,
        run_id: str,
        scenario: str,
        station_id: str,
        stage: str,
        start_ts: float,
        end_ts: float,
        notes: Any = "",
    ):
        self.log_stage(
            run_id=run_id,
            scenario=scenario,
            station_id=station_id,
            stage=stage,
            start_ts=start_ts,
            end_ts=end_ts,
            notes=notes,
        )

    def _serialise_notes(self, notes: Any) -> str:
        if notes is None or notes == "":
            return ""
        if callable(notes):
            notes = notes()
        if isinstance(notes, str):
            return notes
        try:
            return json.dumps(notes, sort_keys=True)
        except TypeError:
            return str(notes)

    def _get_cpu_percent(self):
        if not psutil:
            return ""
        try:
            return round(float(psutil.cpu_percent(interval=None)), 3)
        except Exception:
            return ""

    def _get_memory_mb(self):
        if not self._process:
            return ""
        try:
            rss = self._process.memory_info().rss / (1024 * 1024)
            return round(float(rss), 3)
        except Exception:
            return ""

    def _get_temperature_c(self):
        if not psutil or not hasattr(psutil, "sensors_temperatures"):
            return ""
        try:
            sensors = psutil.sensors_temperatures()
        except Exception:
            return ""

        for entries in sensors.values():
            for entry in entries:
                current = getattr(entry, "current", None)
                if current is not None:
                    return round(float(current), 3)
        return ""


@contextmanager
def profile_stage(
    profiler: Optional[PASOProfiler],
    run_id: str,
    scenario: str,
    station_id: str,
    stage: str,
    notes: Optional[Callable[[], Dict[str, Any]]] = None,
):
    start_ts = time.time()
    start_perf = time.perf_counter()
    try:
        yield
    finally:
        end_ts = time.time()
        end_perf = time.perf_counter()
        if profiler:
            profiler.log_stage(
                run_id=run_id,
                scenario=scenario,
                station_id=station_id,
                stage=stage,
                start_ts=start_ts,
                end_ts=end_ts,
                duration_ms=(end_perf - start_perf) * 1000.0,
                notes=notes,
            )
