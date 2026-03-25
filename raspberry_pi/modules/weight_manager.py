"""
Smart Medication System - Weight Manager Module

Two-phase detection:
  Phase 1 WAITING_FOR_REMOVAL:
      Armed with a baseline. Wait until the bottle is lifted off
      (weight drops below EMPTY_SCALE_THRESHOLD_G).

  Phase 2 WAITING_FOR_REPLACEMENT:
      Bottle is off the scale. Wait until it is placed back and the
      reading is stable again. Then compute:
          delta = baseline_weight - new_stable_weight
      and fire the pill-removal callback with the estimated pill count.

The baseline represents the last authorised stable on-scale weight.
After a confirmed event, the returned bottle weight becomes the new
baseline so later scheduled doses compare against the latest bottle state.
"""

import json
import time
from enum import Enum, auto
from pathlib import Path
from typing import Dict, Any, Callable, Optional


# ------------------------------------------------------------------------------
# Per-station detection state
# ------------------------------------------------------------------------------

class _DetectionState(Enum):
    DISABLED            = auto()   # event detection not armed
    WAITING_FOR_REMOVAL = auto()   # baseline set, waiting for bottle lift
    REMOVED             = auto()   # bottle is off the scale
    WAITING_FOR_STABLE  = auto()   # bottle back on scale, waiting for stable read


# Weight below this value (grams) is treated as "nothing on scale"
EMPTY_SCALE_THRESHOLD_G = 5.0

class WeightManager:
    """Manages weight sensor data and two-phase bottle event detection."""

    def __init__(self, config: dict, logger):
        self.config = config
        self.logger = logger

        # Latest raw data per station
        self.weight_data: Dict[str, Any] = {}

        # Captured baselines (full-bottle weight)
        self.baseline_weights: Dict[str, float] = {}

        # Last confirmed event per station
        self.last_event_data: Dict[str, Any] = {}

        # Per-station detection FSM state
        self._detection_state: Dict[str, _DetectionState] = {}

        # Timestamps / helpers for stable-candidate logic
        self._stable_candidate_start: Dict[str, Optional[float]] = {}
        self._last_event_time: Dict[str, float] = {}

        # Misc flags kept for API compatibility
        self.baseline_capture_required: Dict[str, bool] = {}

        self.pill_removal_callback: Optional[Callable] = None
        self.pill_addition_callback: Optional[Callable] = None  # kept for compat

        # Build per-station dicts
        self.station_configs: Dict[str, dict] = {}
        for _, station_cfg in config.items():
            if isinstance(station_cfg, dict) and "id" in station_cfg:
                sid = station_cfg["id"]
                self.station_configs[sid] = station_cfg
                self._detection_state[sid]       = _DetectionState.DISABLED
                self._stable_candidate_start[sid] = None
                self._last_event_time[sid]        = 0.0
                self.baseline_capture_required[sid] = True

        # Persistence
        self.baseline_file = Path("data/station_baselines.json")
        self.baseline_file.parent.mkdir(parents=True, exist_ok=True)
        self._load_persisted_baselines()

        self.logger.info(
            f"WeightManager initialised for {len(self.station_configs)} station(s)"
        )

    # --------------------------------------------------------------------------
    # Configuration helpers
    # --------------------------------------------------------------------------

    def _get_pill_weight_g(self, station_id: str) -> float:
        cfg = self.station_configs.get(station_id, {})
        return float(cfg.get("pill_weight_mg", 500)) / 1000.0

    def _get_settle_time(self, station_id: str) -> float:
        cfg = self.station_configs.get(station_id, {})
        return float(cfg.get("event_settle_seconds", 1.5))
        
    def _get_cooldown(self, station_id: str) -> float:
        cfg = self.station_configs.get(station_id, {})
        return float(cfg.get("event_cooldown_seconds", 2.0))

    # --------------------------------------------------------------------------
    # Callbacks
    # --------------------------------------------------------------------------

    def set_pill_removal_callback(self, callback: Callable):
        self.pill_removal_callback = callback
        self.logger.info("Pill removal callback registered")

    def set_pill_addition_callback(self, callback: Callable):
        self.pill_addition_callback = callback
        self.logger.info("Pill addition callback registered (compat only)")

    # --------------------------------------------------------------------------
    # Baseline persistence
    # --------------------------------------------------------------------------

    def _load_persisted_baselines(self):
        if not self.baseline_file.exists():
            return
        try:
            with open(self.baseline_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for sid, val in data.items():
                if sid in self.station_configs:
                    self.baseline_weights[sid] = float(val)
                    self.baseline_capture_required[sid] = False
            self.logger.info(f"Loaded persisted baselines: {self.baseline_weights}")
        except Exception as e:
            self.logger.error(f"Failed to load persisted baselines: {e}")

    def _save_persisted_baselines(self):
        try:
            with open(self.baseline_file, "w", encoding="utf-8") as f:
                json.dump(self.baseline_weights, f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save persisted baselines: {e}")
            
    # --------------------------------------------------------------------------
    # Arm / disarm event detection
    # --------------------------------------------------------------------------

    def enable_event_detection(self, station_id: str):
        """Arm two-phase detection for a station (baseline must already exist)."""
        if station_id not in self.station_configs:
            return
        if self.baseline_capture_required.get(station_id, True):
            self.logger.warning(
                f"Cannot arm {station_id}: baseline not yet captured"
            )
            return
        self._detection_state[station_id]        = _DetectionState.WAITING_FOR_REMOVAL
        self._stable_candidate_start[station_id]  = None
        self.logger.info(
            f"Event detection ENABLED for {station_id} "
            f"(baseline={self.baseline_weights.get(station_id):.2f}g)"
        )

    def disable_event_detection(self, station_id: str):
        """Disarm detection for a station."""
        if station_id not in self.station_configs:
            return
        self._detection_state[station_id]        = _DetectionState.DISABLED
        self._stable_candidate_start[station_id]  = None
        self.logger.info(f"Event detection DISABLED for {station_id}")

    def disable_all_event_detection(self):
        for sid in self.station_configs:
            self.disable_event_detection(sid)

    # --------------------------------------------------------------------------
    # Baseline capture
    # --------------------------------------------------------------------------

    def capture_current_baseline(self, station_id: str) -> bool:
        """
        Snapshot the current stable weight as the full-bottle baseline.
        Must be called while the full bottle is sitting still on the scale.
        """
        data = self.weight_data.get(station_id)
        if not data:
            self.logger.warning(f"No weight data for {station_id}")
            return False
        if not data.get("stable", False):
            self.logger.warning(
                f"Cannot capture baseline for {station_id}: reading not stable"
            )
            return False

        weight_g = float(data["weight_g"])
        self.baseline_weights[station_id]          = weight_g
        self.baseline_capture_required[station_id] = False
        self._detection_state[station_id]          = _DetectionState.DISABLED
        self._stable_candidate_start[station_id]   = None
        self._save_persisted_baselines()

        self.logger.info(f"Baseline captured for {station_id}: {weight_g:.2f} g")
        return True
        
    def require_new_baseline(self, station_id: str):
        self.baseline_capture_required[station_id] = True
        self._detection_state[station_id]          = _DetectionState.DISABLED
        self._stable_candidate_start[station_id]   = None
        self.baseline_weights.pop(station_id, None)
        self._save_persisted_baselines()
        self.logger.info(f"{station_id} now requires a new baseline capture")

    # --------------------------------------------------------------------------
    # Main data-processing entry point (called from MQTT callback)
    # --------------------------------------------------------------------------

    def process_weight_data(self, data: Dict[str, Any]):
        try:
            station_id  = data.get("station_id")
            if not station_id or station_id not in self.station_configs:
                return

            raw_weight  = data.get("weight_g")
            if raw_weight is None:
                return

            weight_g    = float(raw_weight)
            stable      = bool(data.get("stable", False))
            received_at = float(data.get("received_at", time.time()))

            # Store latest reading
            self.weight_data[station_id] = {
                **data,
                "weight_g":    weight_g,
                "stable":      stable,
                "received_at": received_at,
            }

            state = self._detection_state.get(station_id, _DetectionState.DISABLED)
            if state == _DetectionState.DISABLED:
                return

            # -- PHASE 1: waiting for the bottle to be lifted ------------------
            if state == _DetectionState.WAITING_FOR_REMOVAL:
                self._handle_waiting_for_removal(
                    station_id, weight_g, stable, received_at
                )

            # -- PHASE 2: bottle is off waiting for it to come back ----------
            elif state == _DetectionState.REMOVED:
                self._handle_removed(station_id, weight_g, stable, received_at)

            # -- PHASE 3: bottle back on waiting for a stable reading --------
            elif state == _DetectionState.WAITING_FOR_STABLE:
                self._handle_waiting_for_stable(
                    station_id, weight_g, stable, received_at
                )

        except Exception as e:
            self.logger.error(f"Error processing weight data: {e}")
            
    # --------------------------------------------------------------------------
    # FSM handlers
    # --------------------------------------------------------------------------

    def _handle_waiting_for_removal(
        self, station_id: str, weight_g: float, stable: bool, received_at: float
    ):
        """Phase 1 detect that the bottle has been removed."""
        if weight_g <= EMPTY_SCALE_THRESHOLD_G:
            self.logger.info(
                f"[{station_id}] Bottle REMOVED "
                f"(weight={weight_g:.2f}g = threshold={EMPTY_SCALE_THRESHOLD_G}g)"
            )
            self._detection_state[station_id]       = _DetectionState.REMOVED
            self._stable_candidate_start[station_id] = None

    def _handle_removed(
        self, station_id: str, weight_g: float, stable: bool, received_at: float
    ):
        """Phase 2 detect that the bottle has been placed back."""
        if weight_g > EMPTY_SCALE_THRESHOLD_G:
            self.logger.info(
                f"[{station_id}] Bottle RETURNED to scale "
                f"(weight={weight_g:.2f}g). Waiting for stable reading"
            )
            self._detection_state[station_id]        = _DetectionState.WAITING_FOR_STABLE
            self._stable_candidate_start[station_id] = received_at if stable else None

    def _handle_waiting_for_stable(
        self, station_id: str, weight_g: float, stable: bool, received_at: float
    ):
        """Phase 3 wait for a stable reading, then compute delta."""
        settle_time = self._get_settle_time(station_id)

        # If reading becomes unstable again, reset the candidate timer
        if not stable:
            if self._stable_candidate_start[station_id] is not None:
                self.logger.debug(
                    f"[{station_id}] Reading became unstable resetting settle timer"
                )
            self._stable_candidate_start[station_id] = None
            return

        # First stable sample after return
        if self._stable_candidate_start[station_id] is None:
            self._stable_candidate_start[station_id] = received_at
            return

        # Not enough settle time yet
        if (received_at - self._stable_candidate_start[station_id]) < settle_time:
            return

        # -- Stable long enough ? fire the event ------------------------------
        cooldown = self._get_cooldown(station_id)
        if (received_at - self._last_event_time[station_id]) < cooldown:
            return

        baseline = self.baseline_weights.get(station_id)
        if baseline is None:
            self.logger.warning(f"[{station_id}] No baseline cannot compute delta")
            self._reset_to_waiting(station_id)
            return

        delta_g = baseline - weight_g  # positive ? pills removed

        self._fire_removal_event(station_id, delta_g, weight_g, baseline, received_at)

        self._last_event_time[station_id]            = received_at
        # Return to WAITING_FOR_REMOVAL so another cycle can happen
        self._reset_to_waiting(station_id)

    def _reset_to_waiting(self, station_id: str):
        """Return FSM to phase-1 without disarming."""
        self._detection_state[station_id]        = _DetectionState.WAITING_FOR_REMOVAL
        self._stable_candidate_start[station_id]  = None

    # --------------------------------------------------------------------------
    # Event firing
    # --------------------------------------------------------------------------
    
    def _get_min_delta_g(self, station_id: str) -> float:
        """
        Minimum weight change (grams) that counts as a real pill removal.
        Defaults to half a pill weight so scale drift never triggers a false event.
        Override with min_delta_g in config if needed.
        """
        cfg = self.station_configs.get(station_id, {})
        if "min_delta_g" in cfg:
            return float(cfg["min_delta_g"])
        return self._get_pill_weight_g(station_id) * 0.5

    def _fire_removal_event(
        self,
        station_id: str,
        delta_g: float,
        new_weight_g: float,
        baseline_g: float,
        received_at: float,
    ):
        pill_weight = self._get_pill_weight_g(station_id)
        min_delta = self._get_min_delta_g(station_id)

        if delta_g <= 0:
            self.logger.info(
                f"[{station_id}] Bottle replaced with no net removal "
                f"(baseline={baseline_g:.2f}g  new={new_weight_g:.2f}g  delta={delta_g:.2f}g)"
            )
            return

        if delta_g < min_delta:
            self.logger.info(
                f"[{station_id}] Delta {delta_g:.2f}g < min_delta {min_delta:.2f}g "
                f"treated as noise, no event fired"
            )
            return

        estimated_pills_float = (delta_g / pill_weight) if pill_weight > 0 else 0.0
        pills_removed = max(1, int(round(estimated_pills_float))) if pill_weight > 0 else 0
        nearest_delta_g = pills_removed * pill_weight
        estimation_error_g = abs(delta_g - nearest_delta_g)

        self.logger.info(
            f"[{station_id}] PILLS REMOVED: {pills_removed} pill(s) "
            f"| baseline={baseline_g:.2f}g  new={new_weight_g:.2f}g  "
            f"delta={delta_g:.2f}g  est={estimated_pills_float:.2f}"
        )

        event_data = {
            "event_type": "removal",
            "station_id": station_id,
            "pills_removed": pills_removed,
            "estimated_pills_float": round(estimated_pills_float, 3),
            "estimation_error_g": round(estimation_error_g, 3),
            "weight_change_g": round(delta_g, 3),
            "delta_g": round(delta_g, 3),
            "previous_baseline_g": round(baseline_g, 3),
            "current_weight_g": round(new_weight_g, 3),
            "pill_weight_g": round(pill_weight, 3),
            "timestamp": time.time(),
        }

        self.last_event_data[station_id] = event_data

        if self.pill_removal_callback:
            try:
                self.pill_removal_callback(event_data)
            except Exception as e:
                self.logger.error(f"Error in pill removal callback: {e}")

        self.baseline_weights[station_id] = new_weight_g
        self.baseline_capture_required[station_id] = False
        self._save_persisted_baselines()
        self.logger.info(
            f"[{station_id}] Baseline updated to returned bottle weight "
            f"{new_weight_g:.2f}g"
        )

    # --------------------------------------------------------------------------
    # Public query API  (unchanged from original)
    # --------------------------------------------------------------------------
    
    def get_current_weight(self, station_id: str) -> Optional[float]:
        data = self.weight_data.get(station_id)
        return data.get("weight_g") if data else None

    def is_stable(self, station_id: str) -> bool:
        data = self.weight_data.get(station_id)
        return data.get("stable", False) if data else False

    def get_station_status(self, station_id: str) -> Dict[str, Any]:
        data = self.weight_data.get(station_id)
        cfg  = self.station_configs.get(station_id, {})

        det_state = self._detection_state.get(station_id, _DetectionState.DISABLED)

        if not data:
            return {
                "station_id":              station_id,
                "status":                  "no_data",
                "connected":               False,
                "baseline_g":              self.baseline_weights.get(station_id),
                "needs_baseline_capture":  self.baseline_capture_required.get(station_id, True),
                "event_detection_enabled": det_state != _DetectionState.DISABLED,
                "detection_phase":         det_state.name,
            }

        last_seen          = data.get("received_at", 0)
        time_since_update  = time.time() - last_seen
        connected          = time_since_update < 30

        return {
            "station_id":              station_id,
            "connected":               connected,
            "weight_g":                data.get("weight_g"),
            "stable":                  data.get("stable", False),
            "baseline_g":              self.baseline_weights.get(station_id),
            "needs_baseline_capture":  self.baseline_capture_required.get(station_id, True),
            "event_detection_enabled": det_state != _DetectionState.DISABLED,
            "detection_phase":         det_state.name,
            "medicine_name":           cfg.get("medicine_name", "Unknown"),
            "last_update_seconds":     round(time_since_update, 2),
            "status":                  "online" if connected else "offline",
        }

    def verify_dosage(
        self, station_id: str, expected_pills: int, tolerance: int = 0
    ) -> Dict[str, Any]:
        event = self.last_event_data.get(station_id)

        if not event:
            return {
                "verified": False,
                "reason": "No recent weight event available",
                "expected": expected_pills,
                "actual": None,
            }

        if event.get("event_type") != "removal":
            return {
                "verified": False,
                "reason": "Last event was not a pill removal",
                "expected": expected_pills,
                "actual": 0,
            }

        actual = int(event.get("pills_removed", 0))
        difference = abs(actual - expected_pills)

        pill_weight_g = float(event.get("pill_weight_g", self._get_pill_weight_g(station_id)))
        actual_delta_g = float(event.get("weight_change_g", 0.0))
        expected_delta_g = expected_pills * pill_weight_g
        delta_error_g = abs(actual_delta_g - expected_delta_g)

        cfg = self.station_configs.get(station_id, {})
        weight_error_g = float(cfg.get("dose_verification_tolerance_g", 0.12))

        verified = (difference <= tolerance) and (delta_error_g <= weight_error_g)

        return {
            "verified": verified,
            "expected": expected_pills,
            "actual": actual,
            "weight_change_g": actual_delta_g,
            "expected_delta_g": round(expected_delta_g, 3),
            "delta_error_g": round(delta_error_g, 3),
            "difference": difference,
            "within_tolerance": verified,
            "status": "correct" if verified else "incorrect",
        }
        
    def reset_station(self, station_id: str):
        self.weight_data.pop(station_id, None)
        self.baseline_weights.pop(station_id, None)
        self.last_event_data.pop(station_id, None)
        self._detection_state[station_id]        = _DetectionState.DISABLED
        self._stable_candidate_start[station_id]  = None
        self._last_event_time[station_id]         = 0.0
        self.baseline_capture_required[station_id] = True
        self._save_persisted_baselines()
        self.logger.info(f"Reset station data for {station_id}")
