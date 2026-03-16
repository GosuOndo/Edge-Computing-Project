"""
Smart Medication System - Weight Manager Module

Model B version:
Each station holds a medicine container.
Events are detected by comparing the new stable container weight
against the last confirmed stable baseline.
Baseline must be explicitly captured before runtime events are enabled.

This version also persists baselines to disk and supports
arming/disarming event detection so the full app only reacts
during an active medication workflow.
"""

import json
import time
from pathlib import Path
from typing import Dict, Any, Callable, Optional


class WeightManager:
    """Manages weight sensor data and Model-B container event detection."""

    def __init__(self, config: dict, logger):
        self.config = config
        self.logger = logger

        self.weight_data = {}
        self.baseline_weights = {}
        self.last_event_data = {}

        self.transition_active = {}
        self.transition_start = {}
        self.stable_candidate_start = {}
        self.last_event_time = {}
        self.baseline_capture_required = {}

        # NEW: event detection arm/disarm flag per station
        self.event_detection_enabled = {}

        self.pill_removal_callback = None
        self.pill_addition_callback = None

        self.station_configs = {}
        for _, station_config in config.items():
            if isinstance(station_config, dict) and "id" in station_config:
                station_id = station_config["id"]
                self.station_configs[station_id] = station_config
                self.transition_active[station_id] = False
                self.transition_start[station_id] = None
                self.stable_candidate_start[station_id] = None
                self.last_event_time[station_id] = 0.0
                self.baseline_capture_required[station_id] = True
                self.event_detection_enabled[station_id] = False

        self.baseline_file = Path("data/station_baselines.json")
        self.baseline_file.parent.mkdir(parents=True, exist_ok=True)

        self._load_persisted_baselines()

        self.logger.info(
            f"Weight manager initialized for {len(self.station_configs)} stations"
        )

    def _get_threshold(self, station_id: str) -> float:
        cfg = self.station_configs.get(station_id, {})
        return float(cfg.get("threshold_delta_g", 0.2))

    def _get_pill_weight(self, station_id: str) -> float:
        cfg = self.station_configs.get(station_id, {})
        pill_weight_mg = float(cfg.get("pill_weight_mg", 500))
        return pill_weight_mg / 1000.0

    def _get_settle_time(self, station_id: str) -> float:
        cfg = self.station_configs.get(station_id, {})
        return float(cfg.get("event_settle_seconds", 1.0))

    def _get_cooldown(self, station_id: str) -> float:
        cfg = self.station_configs.get(station_id, {})
        return float(cfg.get("event_cooldown_seconds", 2.0))

    def set_pill_removal_callback(self, callback):
        self.pill_removal_callback = callback
        self.logger.info("Pill removal callback registered")

    def set_pill_addition_callback(self, callback):
        self.pill_addition_callback = callback
        self.logger.info("Pill addition callback registered")

    def _load_persisted_baselines(self):
        """Load saved baselines from disk."""
        if not self.baseline_file.exists():
            self.logger.info("No persisted baseline file found yet.")
            return
            
        try:
            with open(self.baseline_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                self.logger.warning("Persisted baseline file is not a valid dict.")
                return

            for station_id, baseline in data.items():
                if station_id in self.station_configs:
                    self.baseline_weights[station_id] = float(baseline)
                    self.baseline_capture_required[station_id] = False

            self.logger.info(f"Loaded persisted baselines: {self.baseline_weights}")

        except Exception as e:
            self.logger.error(f"Failed to load persisted baselines: {e}")

    def _save_persisted_baselines(self):
        """Save baselines to disk."""
        try:
            with open(self.baseline_file, "w", encoding="utf-8") as f:
                json.dump(self.baseline_weights, f, indent=2)

            self.logger.info(f"Persisted baselines saved: {self.baseline_weights}")

        except Exception as e:
            self.logger.error(f"Failed to save persisted baselines: {e}")

    # ----------------------------
    # NEW: arm / disarm event detection
    # ----------------------------
    def enable_event_detection(self, station_id: str):
        if station_id in self.event_detection_enabled:
            self.event_detection_enabled[station_id] = True
            self.transition_active[station_id] = False
            self.transition_start[station_id] = None
            self.stable_candidate_start[station_id] = None
            self.logger.info(f"Event detection ENABLED for {station_id}")

    def disable_event_detection(self, station_id: str):
        if station_id in self.event_detection_enabled:
            self.event_detection_enabled[station_id] = False
            self.transition_active[station_id] = False
            self.transition_start[station_id] = None
            self.stable_candidate_start[station_id] = None
            self.logger.info(f"Event detection DISABLED for {station_id}")

    def disable_all_event_detection(self):
        for station_id in self.event_detection_enabled:
            self.disable_event_detection(station_id)

    def capture_current_baseline(self, station_id: str) -> bool:
        """
        Explicitly capture the current stable weight as the baseline
        for the container on that station.
        """
        data = self.weight_data.get(station_id)
        if not data:
            self.logger.warning(f"No weight data available for {station_id}")
            return False

        if not data.get("stable", False):
            self.logger.warning(f"Cannot capture baseline for {station_id}: reading not stable")
            return False

        weight_g = float(data.get("weight_g", 0.0))
        self.baseline_weights[station_id] = weight_g
        self.transition_active[station_id] = False
        self.transition_start[station_id] = None
        self.stable_candidate_start[station_id] = None
        self.baseline_capture_required[station_id] = False

        self._save_persisted_baselines()

        self.logger.info(f"Baseline captured for {station_id}: {weight_g:.2f}g")
        return True

    def require_new_baseline(self, station_id: str):
        """
        Mark a station as needing baseline recapture before runtime events.
        """
        self.baseline_capture_required[station_id] = True
        self.transition_active[station_id] = False
        self.transition_start[station_id] = None
        self.stable_candidate_start[station_id] = None
        self.event_detection_enabled[station_id] = False

        if station_id in self.baseline_weights:
            del self.baseline_weights[station_id]
            self._save_persisted_baselines()

        self.logger.info(f"{station_id} now requires baseline capture")
        
    def process_weight_data(self, data: Dict[str, Any]):
        try:
            station_id = data.get("station_id")
            if not station_id:
                self.logger.warning("Received weight data without station_id")
                return

            if station_id not in self.station_configs:
                self.logger.warning(f"Unknown station ID: {station_id}")
                return

            raw_weight = data.get("weight_g")
            if raw_weight is None:
                self.logger.warning(f"Missing weight_g for {station_id}")
                return

            weight_g = float(raw_weight)
            stable = bool(data.get("stable", False))
            received_at = float(data.get("received_at", time.time()))

            self.weight_data[station_id] = {
                **data,
                "weight_g": weight_g,
                "stable": stable,
                "received_at": received_at
            }

            # Do not detect events if baseline has not been captured
            if self.baseline_capture_required.get(station_id, True):
                return

            # NEW: do not detect events unless explicitly armed
            if not self.event_detection_enabled.get(station_id, False):
                return

            threshold = self._get_threshold(station_id)
            settle_time = self._get_settle_time(station_id)
            cooldown = self._get_cooldown(station_id)

            if station_id not in self.baseline_weights:
                return

            baseline = self.baseline_weights[station_id]
            delta_from_baseline = round(weight_g - baseline, 3)

            if (received_at - self.last_event_time[station_id]) < cooldown:
                return

            if not self.transition_active[station_id]:
                if abs(delta_from_baseline) >= threshold:
                    self.transition_active[station_id] = True
                    self.transition_start[station_id] = received_at
                    self.stable_candidate_start[station_id] = received_at if stable else None
                    self.logger.info(
                        f"Transition started on {station_id}: "
                        f"baseline={baseline:.2f}g current={weight_g:.2f}g "
                        f"delta={delta_from_baseline:.2f}g"
                    )
                return

            if abs(delta_from_baseline) < threshold:
                if stable:
                    self.logger.info(
                        f"Transition cancelled on {station_id}: "
                        f"returned near baseline ({weight_g:.2f}g)"
                    )
                    self.transition_active[station_id] = False
                    self.transition_start[station_id] = None
                    self.stable_candidate_start[station_id] = None
                return

            if not stable:
                self.stable_candidate_start[station_id] = None
                return

            if self.stable_candidate_start[station_id] is None:
                self.stable_candidate_start[station_id] = received_at
                return

            stable_duration = received_at - self.stable_candidate_start[station_id]
            if stable_duration < settle_time:
                return

            self._detect_pill_event(station_id, delta_from_baseline, weight_g, baseline)

            # Update baseline only after a confirmed event while armed
            self.baseline_weights[station_id] = weight_g
            self._save_persisted_baselines()

            self.last_event_time[station_id] = received_at
            self.transition_active[station_id] = False
            self.transition_start[station_id] = None
            self.stable_candidate_start[station_id] = None

        except Exception as e:
            self.logger.error(f"Error processing weight data: {e}")
            
    def _detect_pill_event(
        self,
        station_id: str,
        delta_g: float,
        current_weight_g: float,
        previous_baseline_g: float
    ):
        pill_weight = self._get_pill_weight(station_id)
        if pill_weight <= 0:
            self.logger.warning(f"Invalid pill weight for {station_id}")
            return

        pills_changed = max(1, round(abs(delta_g) / pill_weight))

        if delta_g < 0:
            self.logger.info(
                f"Pill removal detected on {station_id}: "
                f"{pills_changed} pill(s), change={abs(delta_g):.2f}g "
                f"(baseline {previous_baseline_g:.2f}g -> {current_weight_g:.2f}g)"
            )

            event_data = {
                "event_type": "removal",
                "station_id": station_id,
                "pills_removed": pills_changed,
                "weight_change_g": abs(delta_g),
                "delta_g": delta_g,
                "previous_baseline_g": previous_baseline_g,
                "current_weight_g": current_weight_g,
                "timestamp": time.time()
            }

            self.last_event_data[station_id] = event_data

            if self.pill_removal_callback:
                try:
                    self.pill_removal_callback(event_data)
                except Exception as e:
                    self.logger.error(f"Error in pill removal callback: {e}")

        elif delta_g > 0:
            self.logger.info(
                f"Pill addition detected on {station_id}: "
                f"{pills_changed} pill(s), change={delta_g:.2f}g "
                f"(baseline {previous_baseline_g:.2f}g -> {current_weight_g:.2f}g)"
            )

            event_data = {
                "event_type": "addition",
                "station_id": station_id,
                "pills_added": pills_changed,
                "weight_change_g": delta_g,
                "delta_g": delta_g,
                "previous_baseline_g": previous_baseline_g,
                "current_weight_g": current_weight_g,
                "timestamp": time.time()
            }

            self.last_event_data[station_id] = event_data

            if self.pill_addition_callback:
                try:
                    self.pill_addition_callback(event_data)
                except Exception as e:
                    self.logger.error(f"Error in pill addition callback: {e}")

    def get_current_weight(self, station_id: str) -> Optional[float]:
        data = self.weight_data.get(station_id)
        return data.get("weight_g") if data else None

    def is_stable(self, station_id: str) -> bool:
        data = self.weight_data.get(station_id)
        return data.get("stable", False) if data else False

    def get_station_status(self, station_id: str) -> Dict[str, Any]:
        data = self.weight_data.get(station_id)
        cfg = self.station_configs.get(station_id, {})

        if not data:
            return {
                "station_id": station_id,
                "status": "no_data",
                "connected": False,
                "baseline_g": self.baseline_weights.get(station_id),
                "needs_baseline_capture": self.baseline_capture_required.get(station_id, True),
                "event_detection_enabled": self.event_detection_enabled.get(station_id, False)
            }

        last_seen = data.get("received_at", 0)
        time_since_update = time.time() - last_seen
        connected = time_since_update < 30
        
        return {
            "station_id": station_id,
            "connected": connected,
            "weight_g": data.get("weight_g"),
            "stable": data.get("stable", False),
            "baseline_g": self.baseline_weights.get(station_id),
            "needs_baseline_capture": self.baseline_capture_required.get(station_id, True),
            "event_detection_enabled": self.event_detection_enabled.get(station_id, False),
            "medicine_name": cfg.get("medicine_name", "Unknown"),
            "last_update_seconds": round(time_since_update, 2),
            "status": "online" if connected else "offline"
        }

    def verify_dosage(self, station_id: str, expected_pills: int, tolerance: int = 0) -> Dict[str, Any]:
        event = self.last_event_data.get(station_id)

        if not event:
            return {
                "verified": False,
                "reason": "No recent weight event available",
                "expected": expected_pills,
                "actual": None
            }

        if event.get("event_type") != "removal":
            return {
                "verified": False,
                "reason": "Last event was not a pill removal",
                "expected": expected_pills,
                "actual": 0
            }

        actual_removed = int(event.get("pills_removed", 0))
        difference = abs(actual_removed - expected_pills)
        verified = difference <= tolerance

        return {
            "verified": verified,
            "expected": expected_pills,
            "actual": actual_removed,
            "weight_change_g": event.get("weight_change_g", 0.0),
            "difference": difference,
            "within_tolerance": verified,
            "status": "correct" if verified else "incorrect"
        }

    def reset_station(self, station_id: str):
        if station_id in self.weight_data:
            del self.weight_data[station_id]
        if station_id in self.baseline_weights:
            del self.baseline_weights[station_id]
        if station_id in self.last_event_data:
            del self.last_event_data[station_id]

        self.transition_active[station_id] = False
        self.transition_start[station_id] = None
        self.stable_candidate_start[station_id] = None
        self.last_event_time[station_id] = 0.0
        self.baseline_capture_required[station_id] = True
        self.event_detection_enabled[station_id] = False

        self._save_persisted_baselines()

        self.logger.info(f"Reset station data for {station_id}")
