#!/usr/bin/env python3
"""
Smart Medication System - Main Application

Edge-based medication verification system with real-time monitoring.
Orchestrates all modules and handles the complete medication intake workflow.

Tag scan control
-----------------
During normal runtime the tag readers stay enabled so the Pi can continuously
observe which bottle is resting on each station. Onboarding still uses tighter
per-slot scan control inside RegistrationManager to avoid capturing the
outgoing bottle as the next slot's scan.
"""

import sys
import time
import signal
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path

import os
os.environ["SDL_VIDEO_FBDEV"] = "/dev/fb0"

sys.path.insert(0, str(Path(__file__).parent))

from utils.logger import get_logger
from utils.config_loader import get_config
from utils.profiler import PASOProfiler, profile_stage

from services.mqtt_client import MQTTClient
from services.scheduler import MedicationScheduler
from services.state_machine import StateMachine, SystemState

from modules.weight_manager import WeightManager
from modules.medicine_scanner import MedicineScanner
from modules.patient_monitor import PatientMonitor
from modules.telegram_bot import TelegramBot
from modules.display_manager import DisplayManager
from modules.audio_manager import AudioManager
from modules.decision_engine import DecisionEngine, DecisionResult
from modules.database import Database
from modules.tag_runtime_service import TagRuntimeService
from modules.identity_manager import IdentityManager
from modules.registration_manager import RegistrationManager


class MedicationSystem:
    """
    Top-level orchestrator for the Smart Medication Verification System.
    """

    def __init__(
        self,
        config_path: str = "config/config.yaml",
        enable_display: bool = True,
        enable_audio: bool = True
    ):
        self.config = get_config(config_path)
        self.logger = get_logger(self.config.get_logging_config())
        self.logger.info("Smart Medication Verification System starting")
        self.profiler = PASOProfiler(
            self.config.get("profiling", {}).get(
                "paso_output_path", "data/paso_metrics.csv"
            )
        )

        self.enable_display = enable_display
        self.enable_audio   = enable_audio
        self.running        = False
        self._stop_called   = False
        self._paso_run_sequence = 0

        self.state_machine    = StateMachine(self.logger)
        self.current_medication = None

        self.pending_weight_event        = None
        self.pending_weight_lock         = False
        self.pending_manual_reminder     = None
        self.pending_manual_reminder_lock = False
        self.pending_monitoring_ui       = None
        self.secured_medications         = {}
        self._processed_tag_scans        = {}
        self._last_station_scan_audit    = {}
        self.min_secured_bottle_weight_g = float(
            self.config.get("registration", {}).get("min_bottle_weight_g", 5.0)
        )
        self._last_security_violation_message = None
        self._last_idle_minute           = None

        # ------------------------------------------------------------------
        # Dosage retry state
        # Per station, tracks how many pills have been cumulatively detected
        # in the current dose window and how many attempts have been made.
        # Both are reset when a new reminder fires or the cycle ends.
        # ------------------------------------------------------------------
        self._MAX_DOSAGE_ATTEMPTS  = 3
        self._dose_pills_removed: dict = {}   # station_id -> int cumulative
        self._dose_attempt_count: dict = {}   # station_id -> int attempts used

        # True while the station firmware is handling dosing (pill counting
        # on-device).  Prevents the old lift/replace FSM from interfering.
        self._firmware_dosing_active = False

        # ------------------------------------------------------------------
        # Security-aware reminder deferral state
        #
        # When a security alert (missing bottle, wrong bottle, weight
        # tampering) is active on a station at the moment a scheduled
        # reminder fires, the reminder is held here instead of being
        # processed.  Each main-loop tick _process_deferred_reminders()
        # checks whether the alert has cleared (resume reminder) or the
        # caregiver timeout has been exceeded (alert caregiver).
        #
        # Structure:
        #   { medicine_name: {
        #       "reminder_data":  dict,   # original reminder payload
        #       "deferred_at":    float,  # epoch when deferral started
        #       "alert_notified": bool,   # True once caregiver was alerted
        #   } }
        # ------------------------------------------------------------------
        self._deferred_reminders: dict = {}
        reminder_cfg = self.config.get("reminder", {})
        self._security_alert_caregiver_timeout_minutes = float(
            reminder_cfg.get("post_security_alert_timeout_minutes", 15)
        )

        self._initialize_modules()

        signal.signal(signal.SIGINT,  self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    # ------------------------------------------------------------------
    # Module initialisation
    # ------------------------------------------------------------------

    def _initialize_modules(self):
        self.logger.info("Initializing modules...")
        try:
            self.database = Database(self.config["database"], self.logger)
            self.database.connect()

            self.mqtt = MQTTClient(self.config.get_mqtt_config(), self.logger)
            self.mqtt.set_weight_callback(self._on_weight_data)
            self.mqtt.set_status_callback(self._on_status_data)
            self.mqtt.connect()

            self.weight_manager = WeightManager(
                self.config["weight_sensors"], self.logger
            )
            self.weight_manager.set_pill_removal_callback(self._on_pill_removal)
            # Start scanning when the bottle is lifted so the reader
            # is ready to capture the tag when the bottle is placed back.
            self.weight_manager.set_bottle_lifted_callback(self._on_bottle_lifted)
            # Fired when station firmware reports dosing_complete.
            self.weight_manager.set_dosing_complete_callback(
                self._on_dosing_complete
            )

            scanner_config = dict(self.config["ocr"])
            scanner_config.update(self.config["hardware"].get("camera", {}))
            self.scanner = MedicineScanner(scanner_config, self.logger)

            self.patient_monitor = PatientMonitor(
                self.config["patient_monitoring"], self.logger
            )

            self.telegram = TelegramBot(self.config["telegram"], self.logger)
            self.telegram.start_queue_processor()

            if self.enable_display:
                self.display = DisplayManager(
                    self.config["hardware"]["display"], self.logger
                )
                self.display.initialize()
                self._show_idle_screen()
            else:
                self.display = None
                self.logger.info("Display skipped (headless mode)")

            if self.enable_audio:
                self.audio = AudioManager(
                    self.config["hardware"]["audio"], self.logger
                )
                ok = self.audio.initialize()
                if not ok:
                    self.logger.warning(
                        "Audio failed to initialize, continuing without audio"
                    )
            else:
                self.audio = None
                self.logger.info("Audio skipped (headless mode)")

            self.decision_engine = DecisionEngine(
                self.config["decision_engine"], self.logger
            )

            identity_cfg = self.config.get("identity", {})
            tag_cfg      = identity_cfg.get("tag", {})
            tag_topic    = tag_cfg.get("mqtt_topic", "medication/tag/read/+")

            # Build per-station command topics and station->reader mapping from
            # weight_sensors config (each station declares its tag_reader_id).
            weight_cfg = self.config.get("weight_sensors", {})
            station_to_reader: dict = {}
            for _, sc in weight_cfg.items():
                if isinstance(sc, dict) and sc.get("id") and sc.get("tag_reader_id"):
                    station_to_reader[sc["id"]] = sc["tag_reader_id"]

            # command_topics: prefer explicit config block; fall back to
            # deriving topics from station_to_reader mapping.
            cfg_command_topics: dict = tag_cfg.get("command_topics", {})
            if cfg_command_topics:
                command_topics = cfg_command_topics
            elif station_to_reader:
                command_topics = {
                    sid: f"medication/tag/command/{rid}"
                    for sid, rid in station_to_reader.items()
                }
            else:
                # Legacy single-reader fallback
                command_topics = {
                    "_default": tag_cfg.get(
                        "command_topic", "medication/tag/command/tag_reader_1"
                    )
                }

            self.tag_runtime_service = TagRuntimeService(
                mqtt_config=self.config["mqtt"],
                database=self.database,
                logger=self.logger,
                topic=tag_topic,
                command_topics=command_topics,
                station_to_reader=station_to_reader,
            )
            self.tag_runtime_service.start()

            self.identity_manager = IdentityManager(
                config=self.config.config,
                scanner=self.scanner,
                database=self.database,
                tag_runtime_service=self.tag_runtime_service,
                logger=self.logger
            )
            
            self.registration_manager = RegistrationManager(
                config=self.config.config,
                weight_manager=self.weight_manager,
                tag_runtime_service=self.tag_runtime_service,
                database=self.database,
                display=self.display,
                audio=self.audio,
                telegram=self.telegram,
                logger=self.logger
            )

            reminder_cfg = dict(self.config.get("reminder", {}) or {})
            if not reminder_cfg:
                reminder_cfg = dict(
                    ((self.config.get("schedule", {}) or {}).get("reminder", {}) or {})
                )

            schedule_cfg = {
                "medications": [],
                "reminder": reminder_cfg,
            }

            self.scheduler = MedicationScheduler(
                schedule_cfg, self.logger
            )
            self.scheduler.set_reminder_callback(self.queue_manual_reminder)
            self.scheduler.set_missed_dose_callback(self._on_missed_dose)

            self.logger.info("All modules initialized successfully")

        except Exception as e:
            self.logger.critical(f"Module initialization failed: {e}")
            raise

    # ------------------------------------------------------------------
    # Medicine ID resolution
    # ------------------------------------------------------------------

    def _resolve_medicine_id_for_station(
        self, station_id: str, medicine_name: str = None
    ):
        if medicine_name:
            all_registered = self.database.list_registered_medicines()
            for r in all_registered:
                if (r.get("station_id") == station_id and
                        r.get("medicine_name", "").upper() == medicine_name.upper()):
                    return r.get("medicine_id")

        registered = self.database.get_registered_medicine_by_station(station_id)
        if registered:
            return registered.get("medicine_id")
        return None

    def _resolve_record_from_scan(self, scan_msg: dict):
        if not scan_msg:
            return None

        tag_uid = scan_msg.get("tag_uid")
        record  = None
        if tag_uid:
            record = self.database.get_registered_medicine_by_tag_uid(tag_uid)

        if record is None:
            record = self.tag_runtime_service.tag_manager.build_record_from_scan(
                scan_msg
            )
        return record

    def _parse_time_slots(self, raw_slots):
        if isinstance(raw_slots, str):
            return [slot.strip() for slot in raw_slots.split(",") if slot.strip()]
        if isinstance(raw_slots, (list, tuple)):
            return [str(slot).strip() for slot in raw_slots if str(slot).strip()]
        return []

    def _get_next_due_datetime(self, raw_slots, now=None):
        now        = now or datetime.now()
        candidates = []

        for slot in self._parse_time_slots(raw_slots):
            try:
                hour_str, minute_str = slot.split(":", 1)
                hour   = int(hour_str)
                minute = int(minute_str)
            except ValueError:
                self.logger.warning(f"Invalid schedule slot skipped: {slot}")
                continue

            candidate = now.replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            if candidate <= now:
                candidate += timedelta(days=1)
            candidates.append((candidate, slot))

        if not candidates:
            return None, None

        return min(candidates, key=lambda item: item[0])

    def _secure_bottle_until_due(
        self, record: dict, scan_received_at: float, current_weight_g: float
    ):
        station_id    = record.get("station_id")
        medicine_name = record.get("medicine_name") or "Unknown"
        next_due_at = None
        scheduled_time = None
        existing_state = self.secured_medications.get(station_id, {})

        existing_due_ts = float(existing_state.get("next_due_timestamp") or 0.0)
        existing_same_medicine = (
            existing_state.get("medicine_id") == record.get("medicine_id")
            or existing_state.get("tag_uid") == record.get("tag_uid")
        )
        if existing_due_ts > time.time() and existing_same_medicine:
            next_due_at = datetime.fromtimestamp(existing_due_ts)
            scheduled_time = existing_state.get("scheduled_time")
        else:
            next_due_at, scheduled_time = self._get_next_due_datetime(
                record.get("time_slots", "")
            )

        if not station_id or not next_due_at:
            self.logger.warning(
                f"Could not secure bottle for {medicine_name}: "
                "missing station or schedule"
            )
            return

        self.secured_medications[station_id] = {
            "medicine_id":         record.get("medicine_id"),
            "medicine_name":       medicine_name,
            "station_id":          station_id,
            "tag_uid":             record.get("tag_uid"),
            "secured_at":          scan_received_at,
            "secured_weight_g":    current_weight_g,
            "current_weight_g":    current_weight_g,
            "next_due_timestamp":  next_due_at.timestamp(),
            "next_due_display":    next_due_at.strftime("%Y-%m-%d %H:%M:%S"),
            "scheduled_time":      scheduled_time,
            "authorized":          False,
            "present":             True,
            "early_alert_sent":    False,
        }
        self._processed_tag_scans[station_id] = scan_received_at

        self.logger.info(
            f"Secured {medicine_name} on {station_id} until "
            f"{next_due_at.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def _clear_pending_wrong_medicine_audio(self):
        if not getattr(self, "audio", None):
            return
        clear_pending = getattr(self.audio, "clear_pending", None)
        if callable(clear_pending):
            clear_pending(("Wrong medicine detected", "Wrong bottle detected"))

    def _flag_wrong_station_bottle(
        self,
        station_id: str,
        registered: dict,
        scan_received_at: float,
        current_weight_g: float,
        scanned_medicine_name: str,
    ):
        secure_state = self.secured_medications.get(station_id)
        if secure_state is None:
            next_due_at, scheduled_time = self._get_next_due_datetime(
                registered.get("time_slots", "")
            )
            secure_state = {
                "medicine_id":         registered.get("medicine_id"),
                "medicine_name":       registered.get("medicine_name", "Unknown"),
                "station_id":          station_id,
                "tag_uid":             registered.get("tag_uid"),
                "secured_at":          scan_received_at,
                "secured_weight_g":    current_weight_g,
                "current_weight_g":    current_weight_g,
                "next_due_timestamp":  next_due_at.timestamp() if next_due_at else 0.0,
                "next_due_display":    (
                    next_due_at.strftime("%Y-%m-%d %H:%M:%S")
                    if next_due_at else ""
                ),
                "scheduled_time":      scheduled_time,
                "authorized":          False,
                "present":             True,
                "early_alert_sent":    False,
            }
            self.secured_medications[station_id] = secure_state

        was_wrong = secure_state.get("wrong_bottle_on_station", False)
        secure_state["present"] = True
        secure_state["authorized"] = False
        secure_state["early_alert_sent"] = False
        secure_state["current_weight_g"] = current_weight_g
        secure_state["wrong_bottle_on_station"] = True
        secure_state["last_wrong_bottle_scan_at"] = scan_received_at

        expected_name = registered.get("medicine_name", "the correct medicine")
        if not was_wrong and self.audio:
            self.audio.speak_async(
                f"Wrong medicine detected. Please place "
                f"{expected_name} on {station_id}"
            )

        self.logger.warning(
            f"Security violation on {station_id}: wrong bottle present "
            f"(expected {expected_name}, got {scanned_medicine_name or 'unknown'})"
        )

    def _station_has_existing_schedule(self, station_id: str) -> bool:
        """
        Return True when this station already has a usable medication schedule
        in the database.
        """
        registered = self.database.get_registered_medicine_by_station(station_id)
        return bool(
            registered and self._parse_time_slots(registered.get("time_slots"))
        )

    def _enable_continuous_tag_scanning(self, station_id: str = None):
        """Keep NFC readers active during normal runtime."""
        self.tag_runtime_service.start_scanning(station_id)
        if station_id:
            self.logger.info(f"Continuous NFC scanning active on {station_id}")
        else:
            self.logger.info("Continuous NFC scanning active on all stations")

    def _bootstrap_registered_station_security_state(self):
        """
        Seed secured station expectations from registration data so the system
        knows which bottle belongs on which station even when stations are
        empty at startup.
        """
        now_ts = time.time()
        for record in self.database.list_registered_medicines():
            station_id = record.get("station_id")
            if not station_id or station_id in self.secured_medications:
                continue

            next_due_at, scheduled_time = self._get_next_due_datetime(
                record.get("time_slots", "")
            )
            if not next_due_at:
                continue

            status = self.weight_manager.get_station_status(station_id)
            weight_g = float(status.get("weight_g") or 0.0)
            bottle_present = (
                bool(status.get("connected"))
                and weight_g >= self.min_secured_bottle_weight_g
            )

            self.secured_medications[station_id] = {
                "medicine_id":         record.get("medicine_id"),
                "medicine_name":       record.get("medicine_name", "Unknown"),
                "station_id":          station_id,
                "tag_uid":             record.get("tag_uid"),
                "secured_at":          now_ts,
                "secured_weight_g":    weight_g if bottle_present else 0.0,
                "current_weight_g":    weight_g if bottle_present else 0.0,
                "next_due_timestamp":  next_due_at.timestamp(),
                "next_due_display":    next_due_at.strftime("%Y-%m-%d %H:%M:%S"),
                "scheduled_time":      scheduled_time,
                "authorized":          False,
                "present":             bottle_present,
                "early_alert_sent":    not bottle_present,
                "early_alert_sent_at": now_ts if not bottle_present else 0.0,
            }

            if not bottle_present:
                self.logger.warning(
                    f"Startup check: {record.get('medicine_name', 'Medicine')} "
                    f"is missing from {station_id}"
                )
            else:
                self.logger.info(
                    f"Startup check: expected bottle for {station_id} is present; "
                    "awaiting NFC verification"
                )

    def _audit_occupied_stations_with_nfc(self, audit_interval_seconds: float = 5.0):
        """
        Periodically force a fresh NFC read for occupied stations so bottle
        identity is re-verified even when the bottle has not been lifted.
        """
        now_ts = time.time()

        for station_id in self.weight_manager.station_configs:
            if (
                self.current_medication
                and self.current_medication.get("station_id") == station_id
            ):
                continue

            status = self.weight_manager.get_station_status(station_id)
            if not status.get("connected"):
                continue

            weight_g = float(status.get("weight_g") or 0.0)
            if weight_g < self.min_secured_bottle_weight_g:
                continue

            latest = self.tag_runtime_service.get_latest_scan(station_id)
            latest_ts = float(latest.get("received_at", 0.0)) if latest else 0.0
            processed_ts = self._processed_tag_scans.get(station_id, 0.0)
            if latest_ts > processed_ts:
                continue

            if (
                now_ts - self._last_station_scan_audit.get(station_id, 0.0)
                < audit_interval_seconds
            ):
                continue

            self.logger.info(
                f"Forcing fresh NFC audit on {station_id} while bottle remains on scale"
            )
            self.tag_runtime_service.clear_latest_scan(station_id)
            self.tag_runtime_service.start_scanning(station_id)
            self._last_station_scan_audit[station_id] = now_ts

    def _process_secured_bottle_placements(self):
        state_changed = False

        for station_id in self.weight_manager.station_configs:
            latest = self.tag_runtime_service.get_latest_scan(station_id)
            if not latest:
                continue

            scan_received_at = float(latest.get("received_at", 0.0))
            if scan_received_at <= self._processed_tag_scans.get(station_id, 0.0):
                continue

            if (
                self.current_medication
                and self.current_medication.get("station_id") == station_id
            ):
                continue

            # Skip stations that are in the middle of verifying a returned bottle,
            # waiting for a stable weight reading for a tamper check, already
            # showing a tamper alert, or have a wrong bottle on station.
            # _process_secured_bottle_movements owns these state transitions;
            # calling _secure_bottle_until_due here would wipe the pre-removal
            # snapshot / tamper flags and drop the alert screen.
            _existing = self.secured_medications.get(station_id, {})
            if (
                _existing.get("early_alert_sent", False)
                or _existing.get("wrong_bottle_on_station", False)
                or _existing.get("tamper_check_pending", False)
                or self._tamper_alert_active(_existing)
            ):
                continue

            scan_msg = latest.get("scan_msg") or {}

            # Reject scans that did not come from this station's own reader.
            # If the station->reader mapping is known, a scan from any other
            # reader (including the global fallback) is ignored here.
            expected_reader_id = self.tag_runtime_service._station_to_reader.get(station_id)
            if expected_reader_id:
                actual_reader_id = scan_msg.get("reader_id")
                if actual_reader_id != expected_reader_id:
                    self.logger.debug(
                        f"[PLACEMENT] Ignoring scan from reader={actual_reader_id} "
                        f"for station={station_id} (expected reader={expected_reader_id})"
                    )
                    continue
            record   = self._resolve_record_from_scan(scan_msg)
            if not record:
                continue

            status = self.weight_manager.get_station_status(station_id)
            if not status.get("connected"):
                continue

            weight_g = float(status.get("weight_g") or 0.0)
            if weight_g < self.min_secured_bottle_weight_g:
                continue

            # Only accept the medicine that is registered to this station.
            # If a different bottle is placed (e.g. aspirin on a paracetamol
            # station), reject it immediately so it cannot overwrite the
            # secured state.
            registered = self.database.get_registered_medicine_by_station(station_id)
            if registered:
                registered_medicine_id = registered.get("medicine_id")
                registered_tag_uid     = registered.get("tag_uid")
                scanned_medicine_id    = record.get("medicine_id")
                scanned_tag_uid        = record.get("tag_uid") or scan_msg.get("tag_uid")
                match = (
                    (registered_medicine_id and scanned_medicine_id == registered_medicine_id)
                    or (registered_tag_uid and scanned_tag_uid == registered_tag_uid)
                )
                self._processed_tag_scans[station_id] = scan_received_at
                if not match:
                    self._flag_wrong_station_bottle(
                        station_id=station_id,
                        registered=registered,
                        scan_received_at=scan_received_at,
                        current_weight_g=weight_g,
                        scanned_medicine_name=record.get("medicine_name"),
                    )
                    state_changed = True
                    self.tag_runtime_service.start_scanning(station_id)
                    continue

            if record.get("station_id") != station_id:
                continue
            if not status.get("stable", False):
                continue

            was_wrong_bottle = bool(
                self.secured_medications.get(station_id, {}).get("wrong_bottle_on_station")
            )

            self._secure_bottle_until_due(record, scan_received_at, weight_g)
            if was_wrong_bottle:
                self._clear_pending_wrong_medicine_audio()
            state_changed = True

        if state_changed:
            self._refresh_security_violation_screen()

    def _notify_unauthorized_bottle_movement(self, secure_state: dict):
        medicine_name = secure_state.get("medicine_name", "medication")
        station_id    = secure_state.get("station_id", "unknown station")
        allowed_time  = secure_state.get("next_due_display", "scheduled time")
        detected_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self.logger.warning(
            f"Unauthorized bottle movement detected for {medicine_name} on "
            f"{station_id} before {allowed_time}"
        )
        self.telegram.send_unauthorized_bottle_movement_alert(
            medicine_name=medicine_name,
            station_id=station_id,
            allowed_time=allowed_time,
            detected_time=detected_time,
        )

    def _tamper_alert_active(self, secure_state: dict) -> bool:
        return bool(secure_state.get("tamper_alert_sent", False))

    def _has_security_alert_for_station(self, station_id: str) -> bool:
        """Return True if *station_id* has any active security alert.

        Covers all three alert types that should pause a medication reminder:
        - Bottle missing (early_alert_sent)
        - Wrong medicine bottle detected (wrong_bottle_on_station)
        - Weight tampering detected after bottle return (tamper_alert_sent)
        """
        secure_state = self.secured_medications.get(station_id)
        if not secure_state:
            return False
        return (
            secure_state.get("early_alert_sent", False)
            or secure_state.get("wrong_bottle_on_station", False)
            or secure_state.get("tamper_check_pending", False)
            or self._tamper_alert_active(secure_state)
        )

    def _clear_tamper_alert_state(self, secure_state: dict):
        secure_state.pop("tamper_alert_sent", None)
        secure_state.pop("tamper_alert_until", None)
        secure_state.pop("tamper_delta_g", None)
        secure_state.pop("tamper_pills_est", None)

    def _station_has_normal_security_window(
        self, station_id: str, secure_state: dict, now_ts: float
    ) -> bool:
        # Keep the security monitoring loop running for this station even
        # when the scheduled dose time has passed, provided a non-tamper
        # alert (missing or wrong bottle) is still active.  This allows
        # _process_secured_bottle_movements to continue tracking the bottle
        # and clear the alert when the correct bottle is returned, which in
        # turn lets _process_deferred_reminders resume the held reminder.
        # Skip this override when an active dosing session is already
        # running for this station (handled by the verification pipeline).
        if (
            secure_state.get("early_alert_sent", False)
            or secure_state.get("wrong_bottle_on_station", False)
        ):
            if not (
                self.current_medication
                and self.current_medication.get("station_id") == station_id
            ):
                return True

        if now_ts >= secure_state.get("next_due_timestamp", 0):
            return False
        if secure_state.get("authorized", False):
            return False
        if (
            self.current_medication
            and self.current_medication.get("station_id") == station_id
        ):
            return False
        return True

    def _sync_station_baseline_weight(self, station_id: str, weight_g: float):
        if weight_g <= 0:
            return

        previous_weight = self.weight_manager.baseline_weights.get(station_id)
        if previous_weight is not None and abs(previous_weight - weight_g) < 0.01:
            return

        self.weight_manager.baseline_weights[station_id] = weight_g
        self.weight_manager._save_persisted_baselines()

    def _assess_returned_bottle_weight(
        self, secure_state: dict, returned_weight_g: float, log_result: bool = True
    ):
        station_id    = secure_state.get("station_id", "unknown")
        medicine_name = secure_state.get("medicine_name", "Unknown")

        reference_g = secure_state.get("pre_removal_weight_g")
        if not reference_g or reference_g <= 0:
            if log_result:
                self.logger.info(
                    f"[{station_id}] No pre-removal weight snapshot for "
                    f"{medicine_name} — skipping tamper check"
                )
            return None

        delta_g = reference_g - returned_weight_g   # positive = bottle is lighter

        # Tamper threshold: station-level override, or half a pill weight
        # (same heuristic used by WeightManager for noise rejection).
        cfg         = self.weight_manager.station_configs.get(station_id, {})
        threshold_g = float(
            cfg.get("tamper_tolerance_g")
            or self.weight_manager._get_min_delta_g(station_id)
        )

        if log_result:
            self.logger.info(
                f"[{station_id}] Tamper check: "
                f"ref={reference_g:.2f}g  returned={returned_weight_g:.2f}g  "
                f"delta={delta_g:.2f}g  threshold={threshold_g:.2f}g"
            )

        pill_weight_g   = self.weight_manager._get_pill_weight_g(station_id)
        estimated_pills = (
            max(1, round(delta_g / pill_weight_g))
            if pill_weight_g > 0 else "unknown"
        )
        return {
            "station_id":       station_id,
            "medicine_name":    medicine_name,
            "reference_g":      reference_g,
            "delta_g":          delta_g,
            "threshold_g":      threshold_g,
            "estimated_pills":  estimated_pills,
        }

    def _has_pending_security_violation(self) -> bool:
        now_ts = time.time()

        for station_id, secure_state in self.secured_medications.items():
            if self._tamper_alert_active(secure_state):
                return True
            if not self._station_has_normal_security_window(
                station_id, secure_state, now_ts
            ):
                continue
            if secure_state.get("tamper_check_pending", False):
                return True
            if secure_state.get("early_alert_sent", False):
                return True
            if secure_state.get("wrong_bottle_on_station", False):
                return True

        return False

    def _get_station_security_issue(self, secure_state: dict):
        if self._tamper_alert_active(secure_state):
            return "tampered"
        if secure_state.get("wrong_bottle_on_station", False):
            return "incorrect"
        if secure_state.get("early_alert_sent", False):
            return "missing"
        if secure_state.get("tamper_check_pending", False):
            return "missing"
        return None

    def _build_security_violation_key(self, issues: dict) -> str:
        """
        Build a stable string key from the issues dict so we can detect when
        the set of violations changes and redraw only when necessary.
        """
        parts = []
        for station_id in sorted(issues):
            parts.append(f"{station_id}:{issues[station_id]}")
        return "|".join(parts)

    def _build_security_violation_issues(self, issues: dict) -> list:
        """
        Enrich the bare {station_id: issue_type} dict with medicine name,
        station label, and scheduled time so the display can show meaningful
        context rather than just station IDs.
        """
        detailed = []
        for station_id in sorted(issues):
            secure_state   = self.secured_medications.get(station_id, {})
            medicine_name  = secure_state.get("medicine_name", "Unknown medicine")
            scheduled_time = secure_state.get("scheduled_time", "")
            station_label  = station_id.replace("_", " ").title()
            issue          = issues[station_id]

            entry = {
                "station_id":     station_id,
                "station_label":  station_label,
                "medicine_name":  medicine_name,
                "issue":          issue,
                "scheduled_time": scheduled_time,
            }

            # Carry tamper-specific detail so the display can show weight delta.
            if issue == "tampered":
                entry["tamper_delta_g"]   = secure_state.get("tamper_delta_g", 0.0)
                entry["tamper_pills_est"] = secure_state.get("tamper_pills_est", "?")

            detailed.append(entry)
        return detailed

    def _refresh_security_violation_screen(self):
        issues = {}
        now_ts = time.time()

        for station_id, secure_state in self.secured_medications.items():
            if (
                not self._tamper_alert_active(secure_state)
                and not self._station_has_normal_security_window(
                    station_id, secure_state, now_ts
                )
            ):
                continue

            issue = self._get_station_security_issue(secure_state)
            if issue:
                issues[station_id] = issue

        if not issues:
            # All violations cleared – return to idle immediately if the
            # alert screen was previously being shown.
            if self._last_security_violation_message is not None:
                self._last_security_violation_message = None
                if getattr(self, "display", None):
                    self._show_idle_screen()
            else:
                self._last_security_violation_message = None
            return

        # Use a stable key to avoid unnecessary redraws.
        key = self._build_security_violation_key(issues)
        if not key:
            self._last_security_violation_message = None
            return

        if (
            getattr(self, "display", None)
            and key != self._last_security_violation_message
        ):
            detailed = self._build_security_violation_issues(issues)
            self.display.show_security_alert_screen(detailed)
        self._last_security_violation_message = key

    def _prompt_return_bottle_to_station(self, secure_state: dict):
        station_id    = secure_state.get("station_id", "station")
        medicine_name = secure_state.get("medicine_name", "medication")
        message = (
            f"{medicine_name} removed from {station_id}. "
            f"Place it back on the correct station."
        )

        self.logger.info("Prompting patient to return the bottle to the station")

        if getattr(self, "audio", None):
            self.audio.speak_async(message)

    def _check_returned_bottle_weight(
        self, secure_state: dict, returned_weight_g: float
    ):
        """
        Compare *returned_weight_g* against the snapshot taken the moment the
        bottle went missing.  A significant loss indicates pills may have been
        removed while the bottle was off the station.

        Outcome A – within tolerance  : log pass, update baseline, return.
        Outcome B – exceeds tolerance : audio alert, Telegram to caregiver,
                    mark tamper state so the shared security-alert screen stays
                    visible until the bottle weight is restored within tolerance,
                    update baseline to reflect actual bottle state.
        """
        assessment = self._assess_returned_bottle_weight(secure_state, returned_weight_g)
        if not assessment:
            return
        station_id    = assessment["station_id"]
        medicine_name = assessment["medicine_name"]
        reference_g   = assessment["reference_g"]
        delta_g       = assessment["delta_g"]
        threshold_g   = assessment["threshold_g"]
        estimated_pills = assessment["estimated_pills"]

        # Always bring the weight_manager baseline in line with what is
        # physically on the scale now so future dose calculations are accurate.
        self._sync_station_baseline_weight(station_id, returned_weight_g)

        if delta_g <= threshold_g:
            self.logger.info(
                f"[{station_id}] Tamper check PASSED — "
                "returned weight within tolerance"
            )
            if self._tamper_alert_active(secure_state):
                self.logger.info(
                    f"[{station_id}] Tamper alert cleared — "
                    "weight is back within tolerance"
                )
                self._clear_tamper_alert_state(secure_state)
                self._refresh_security_violation_screen()
            return

        # ---- Weight discrepancy detected ----
        station_label = station_id.replace("_", " ").title()

        self.logger.warning(
            f"[{station_id}] TAMPER ALERT: {medicine_name} returned "
            f"{delta_g:.2f}g lighter than pre-removal weight "
            f"(~{estimated_pills} pill(s) potentially removed outside dose window)"
        )

        # Audio
        if self.audio:
            self.audio.speak_async(
                f"Warning. {medicine_name} bottle weight does not match. "
                f"Approximately {estimated_pills} pill may have been removed. "
                "Your caregiver has been notified."
            )

        # Telegram caregiver alert
        self.telegram.send_bottle_tampering_alert(
            medicine_name=medicine_name,
            station_id=station_id,
            station_label=station_label,
            reference_weight_g=reference_g,
            returned_weight_g=returned_weight_g,
            delta_g=delta_g,
            estimated_pills_removed=estimated_pills,
        )

        # Flag tamper state so the shared security-alert screen keeps showing
        # until the bottle weight returns within tolerance.
        secure_state["tamper_alert_sent"]  = True
        secure_state["tamper_delta_g"]     = round(delta_g, 2)
        secure_state["tamper_pills_est"]   = estimated_pills
        # Immediately refresh the security screen to show the tamper alert.
        self._refresh_security_violation_screen()

    def _recheck_active_tamper_alert(
        self, secure_state: dict, current_weight_g: float
    ):
        assessment = self._assess_returned_bottle_weight(
            secure_state, current_weight_g, log_result=False
        )
        if not assessment:
            return

        station_id   = assessment["station_id"]
        delta_g      = assessment["delta_g"]
        threshold_g  = assessment["threshold_g"]

        self._sync_station_baseline_weight(station_id, current_weight_g)

        if delta_g <= threshold_g:
            self.logger.info(
                f"[{station_id}] Tamper condition resolved — "
                "returned weight is now within tolerance"
            )
            self._clear_tamper_alert_state(secure_state)
            return

        secure_state["tamper_delta_g"]   = round(delta_g, 2)
        secure_state["tamper_pills_est"] = assessment["estimated_pills"]

    def _verify_returned_bottle(self, secure_state: dict):
        """
        Check that the bottle placed back after early removal is the correct one
        by verifying a tag scan received since the removal alert was sent.
        Returns True when the correct bottle is confirmed, False when a wrong
        bottle is confirmed, or None when the system is still waiting for a
        fresh readable tag from that same station.
        """
        station_id    = secure_state.get("station_id", "unknown")
        alert_sent_at = secure_state.get("early_alert_sent_at", 0.0)

        latest = self.tag_runtime_service.get_latest_scan(station_id)
        if not latest or float(latest.get("received_at", 0.0)) <= alert_sent_at:
            self.logger.info(
                f"Bottle returned to {station_id} but no fresh station-specific "
                "tag scan is available yet"
            )
            return None

        scan_msg = latest.get("scan_msg") or {}

        # Reject scans that did not come from this station's own reader.
        expected_reader_id = self.tag_runtime_service._station_to_reader.get(station_id)
        if expected_reader_id:
            actual_reader_id = scan_msg.get("reader_id")
            if actual_reader_id != expected_reader_id:
                self.logger.warning(
                    f"[VERIFY RETURN] Ignoring scan from reader={actual_reader_id} "
                    f"for station={station_id} (expected reader={expected_reader_id})"
                )
                return None

        record = self._resolve_record_from_scan(scan_msg)

        # Mark this scan as processed so _process_secured_bottle_placements
        # does not treat it as a new onboarding placement.
        self._processed_tag_scans[station_id] = float(latest.get("received_at", 0.0))

        if not record:
            self.logger.warning(
                f"Bottle returned to {station_id} but tag scan could not be resolved"
            )
            self.tag_runtime_service.clear_latest_scan(station_id)
            return None

        expected_medicine_id = secure_state.get("medicine_id")
        expected_tag_uid     = secure_state.get("tag_uid")
        actual_medicine_id   = record.get("medicine_id")
        actual_tag_uid       = record.get("tag_uid") or scan_msg.get("tag_uid")

        # tag_uid is hardware-unique and directly identifies the physical bottle.
        # Use it as the primary check. Only fall back to medicine_id when no
        # tag_uid was recorded during registration (should be rare).
        if expected_tag_uid:
            correct = (actual_tag_uid == expected_tag_uid)
        elif expected_medicine_id:
            correct = (actual_medicine_id == expected_medicine_id)
        else:
            correct = False

        if correct:
            self.logger.info(
                f"Correct bottle returned to {station_id}: "
                f"{secure_state.get('medicine_name')}"
            )
        else:
            self.logger.warning(
                f"Wrong bottle returned to {station_id}! "
                f"Expected {secure_state.get('medicine_name')} ({expected_medicine_id}), "
                f"got {record.get('medicine_name')} ({actual_medicine_id})"
            )
            if self.audio:
                self.audio.speak_async(
                    f"Wrong bottle detected. Please replace with "
                    f"{secure_state.get('medicine_name', 'the correct medication')}"
                )

        self._enable_continuous_tag_scanning(station_id)
        return correct

    def _process_secured_bottle_movements(self):
        now_ts = time.time()

        for station_id, secure_state in self.secured_medications.items():
            status   = self.weight_manager.get_station_status(station_id)
            weight_g = float(status.get("weight_g") or 0.0)
            bottle_present = (
                bool(status.get("connected"))
                and weight_g >= self.min_secured_bottle_weight_g
            )

            if self._tamper_alert_active(secure_state):
                secure_state["present"] = bottle_present
                if bottle_present and status.get("stable", False):
                    secure_state["current_weight_g"] = weight_g
                    self._recheck_active_tamper_alert(secure_state, weight_g)
                else:
                    secure_state.pop("bottle_returned_at", None)
                continue

            if not self._station_has_normal_security_window(
                station_id, secure_state, now_ts
            ):
                continue

            if bottle_present:
                if (
                    not secure_state.get("present", False)
                    and secure_state.get("early_alert_sent", False)
                    and not secure_state.get("wrong_bottle_on_station", False)
                ):
                    # Record the moment the bottle first landed back on the scale.
                    # We wait 2 s before verifying so the RFID reader has time to
                    # send its scan - without this delay the weight sensor wins the
                    # race and _verify_returned_bottle sees no scan yet, causing it
                    # to accept the bottle gracefully before the tag arrives.
                    if secure_state.get("bottle_returned_at") is None:
                        secure_state["bottle_returned_at"] = time.time()

                    if time.time() - secure_state["bottle_returned_at"] < 2.0:
                        continue   # still waiting for RFID scan to arrive

                    # Bottle returned after early removal - verify it's the correct one
                    correct = self._verify_returned_bottle(secure_state)
                    if correct is None:
                        continue

                    secure_state.pop("bottle_returned_at", None)

                    if correct:
                        # Identity confirmed – now check whether the returned
                        # bottle weighs the same as when it was removed.  If the
                        # reading is already stable we do it immediately;
                        # otherwise we defer until the first stable reading.
                        if status.get("stable", False):
                            self._check_returned_bottle_weight(secure_state, weight_g)
                            secure_state["tamper_check_pending"] = False
                        else:
                            self.logger.info(
                                f"[{station_id}] Weight not yet stable – "
                                "deferring tamper check to next stable reading"
                            )
                            secure_state["tamper_check_pending"] = True

                        # Reset so future removals trigger alerts and scanning again.
                        secure_state["early_alert_sent"] = False
                        secure_state["present"] = True
                        secure_state["wrong_bottle_on_station"] = False
                        if status.get("stable", False):
                            secure_state["current_weight_g"] = weight_g
                        if (
                            getattr(self, "display", None)
                            and not self._has_pending_security_violation()
                        ):
                            self._show_idle_screen()
                    else:
                        # Wrong bottle - flag it so we don't re-verify every tick.
                        # Restart scanning so a new scan is captured when the
                        # correct bottle is placed.
                        secure_state["wrong_bottle_on_station"] = True
                        self.tag_runtime_service.start_scanning(station_id)
                        self.tag_runtime_service.clear_latest_scan(station_id)
                elif not secure_state.get("wrong_bottle_on_station", False):
                    secure_state["present"] = True
                    if status.get("stable", False):
                        secure_state["current_weight_g"] = weight_g
                        # Execute any deferred tamper check on the first stable
                        # reading after the correct bottle was returned.
                        if secure_state.get("tamper_check_pending", False):
                            self._check_returned_bottle_weight(secure_state, weight_g)
                            secure_state["tamper_check_pending"] = False
                            if (
                                getattr(self, "display", None)
                                and not self._has_pending_security_violation()
                            ):
                                self._show_idle_screen()
                continue

            # Bottle not present - clear flags so the next placement
            # triggers a fresh verification attempt.
            if secure_state.get("wrong_bottle_on_station", False):
                secure_state["wrong_bottle_on_station"] = False
            secure_state.pop("bottle_returned_at", None)

            if secure_state.get("present", False) and not secure_state.get(
                "early_alert_sent", False
            ):
                # Snapshot the last known stable weight so we can compare it
                # against the weight when the bottle is returned later.
                secure_state["pre_removal_weight_g"] = secure_state.get("current_weight_g")
                # Clear any stale tamper state from a previous removal cycle.
                secure_state.pop("tamper_alert_sent",  None)
                secure_state.pop("tamper_alert_until", None)
                secure_state.pop("tamper_delta_g",     None)
                secure_state.pop("tamper_pills_est",   None)
                secure_state.pop("tamper_check_pending", None)

                self._notify_unauthorized_bottle_movement(secure_state)
                self._prompt_return_bottle_to_station(secure_state)
                secure_state["early_alert_sent"]    = True
                secure_state["early_alert_sent_at"] = time.time()
                self.tag_runtime_service.start_scanning(station_id)

            secure_state["present"] = False

        self._refresh_security_violation_screen()

    def _authorize_current_medication_if_ready(self):
        if not self.current_medication:
            return False

        # Firmware-driven dosing: the M5StickC handles pill counting.
        # The weight FSM does not need to be armed.
        if self._firmware_dosing_active:
            return True

        if self.state_machine.get_state() != SystemState.REMINDER_ACTIVE:
            return False

        station_id = self.current_medication["station_id"]
        status     = self.weight_manager.get_station_status(station_id)
        if status.get("event_detection_enabled"):
            return True
        if not status.get("connected"):
            return False

        # Fast path: _recapture_fresh_baseline() already captured a
        # settle-verified baseline for this dose window.
        if not self.weight_manager.baseline_capture_required.get(station_id, True):
            self.weight_manager.enable_event_detection(station_id)
            secure_state = self.secured_medications.get(station_id)
            if secure_state:
                secure_state["authorized"]            = True
                secure_state["authorized_at"]         = time.time()
                secure_state["authorized_baseline_g"] = (
                    self.weight_manager.baseline_weights.get(station_id)
                )
            self.logger.info(
                f"Authorized bottle removal for {station_id} (fresh baseline="
                f"{self.weight_manager.baseline_weights.get(station_id):.2f}g)"
            )
            return True

        # Slow path: recapture was not possible (bottle absent, sensor offline,
        # or timeout). Fall back to a single-sample stable check.
        weight_g = float(status.get("weight_g") or 0.0)
        if weight_g < self.min_secured_bottle_weight_g:
            return False
        if not status.get("stable", False):
            return False

        if not self.weight_manager.capture_current_baseline(station_id):
            return False

        self.weight_manager.enable_event_detection(station_id)

        secure_state = self.secured_medications.get(station_id)
        if secure_state:
            secure_state["authorized"]             = True
            secure_state["authorized_at"]          = time.time()
            secure_state["authorized_baseline_g"]  = (
                self.weight_manager.baseline_weights.get(station_id)
            )

        self.logger.info(
            f"Authorized bottle removal for {station_id} (fallback baseline="
            f"{self.weight_manager.baseline_weights.get(station_id):.2f}g)"
        )
        return True
        
    # ------------------------------------------------------------------
    # Manual reminder injection (test scripts)
    # ------------------------------------------------------------------

    def queue_manual_reminder(self, reminder_data: dict):
        self.pending_manual_reminder = reminder_data
        self.logger.info(f"Manual reminder queued: {reminder_data}")

    def _process_pending_manual_reminder(self):
        if self.pending_manual_reminder_lock or not self.pending_manual_reminder:
            return
        reminder_data = self.pending_manual_reminder
        station_id    = reminder_data.get("station_id")
        medicine_name = reminder_data.get("medicine_name", "")

        # ----------------------------------------------------------------
        # Security takes precedence over scheduled medication reminders.
        #
        # If the station has an active security alert (missing bottle, wrong
        # bottle, or weight tampering), hold the reminder in
        # _deferred_reminders so the security alert screen remains visible
        # and the patient is not confused by two competing events.
        # _process_deferred_reminders() will resume it when the alert
        # clears, or escalate to the caregiver after a timeout.
        # ----------------------------------------------------------------
        if station_id and self._has_security_alert_for_station(station_id):
            if medicine_name not in self._deferred_reminders:
                self._deferred_reminders[medicine_name] = {
                    "reminder_data":  reminder_data,
                    "deferred_at":    time.time(),
                    "alert_notified": False,
                }
                self.logger.warning(
                    f"[SECURITY PRIORITY] Reminder for {medicine_name} on "
                    f"{station_id} paused — security alert is active. "
                    "Dose schedule held until the security issue is resolved."
                )
            # Clear the pending slot (the scheduler only queues once per
            # scheduled time slot, so this won't discard a future reminder).
            self.pending_manual_reminder = None
            return

        # If this reminder was previously deferred, log that it is resuming.
        if medicine_name in self._deferred_reminders:
            self.logger.info(
                f"[SECURITY RESOLVED] Security alert cleared — "
                f"resuming deferred reminder for {medicine_name}."
            )
            del self._deferred_reminders[medicine_name]

        self.pending_manual_reminder = None
        self.pending_manual_reminder_lock = True
        try:
            self._on_medication_reminder(reminder_data)
        finally:
            self.pending_manual_reminder_lock = False

    # ------------------------------------------------------------------
    # Security-aware reminder deferral
    # ------------------------------------------------------------------

    def _process_deferred_reminders(self):
        """Resume or escalate reminders that were paused by a security alert.

        Called every main-loop tick.  For each deferred reminder:

        1. If the security alert on the station has **cleared**, re-queue
           the reminder for immediate processing so the patient can still
           take their medication (possibly a little late).

        2. If the security alert is **still active** and the caregiver
           timeout has been exceeded, send a missed-dose alert to the
           caregiver (once only) and log the event to the database.
        """
        if not self._deferred_reminders:
            return

        now       = time.time()
        timeout_s = self._security_alert_caregiver_timeout_minutes * 60

        for medicine_name, deferred in list(self._deferred_reminders.items()):
            reminder_data = deferred["reminder_data"]
            station_id    = reminder_data.get("station_id")

            if not self._has_security_alert_for_station(station_id):
                # Alert resolved — but also confirm the bottle is physically
                # back on the station before resuming the reminder.  Without
                # this check a cleared alert (e.g. via _secure_bottle_until_due)
                # on an empty station would launch a dosing flow with no bottle.
                station_status = self.weight_manager.get_station_status(station_id) if station_id else {}
                bottle_present = (
                    bool(station_status.get("connected"))
                    and float(station_status.get("weight_g") or 0.0) >= self.min_secured_bottle_weight_g
                )
                if not bottle_present:
                    self.logger.info(
                        f"[SECURITY RESOLVED] Alert cleared on {station_id} but "
                        f"bottle not yet present — keeping reminder for "
                        f"{medicine_name} deferred."
                    )
                    continue

                self.logger.info(
                    f"[SECURITY RESOLVED] Alert cleared on {station_id}. "
                    f"Resuming deferred reminder for {medicine_name}."
                )
                del self._deferred_reminders[medicine_name]
                if not self.pending_manual_reminder:
                    self.pending_manual_reminder = reminder_data
                return  # Process one at a time; next tick handles the rest.

            # Alert still active — escalate to caregiver after timeout.
            elapsed = now - deferred["deferred_at"]
            if elapsed >= timeout_s and not deferred.get("alert_notified", False):
                self.logger.warning(
                    f"[SECURITY PRIORITY] {medicine_name} not taken for "
                    f"{self._security_alert_caregiver_timeout_minutes} min "
                    f"while security alert is active on {station_id} — "
                    "notifying caregiver."
                )
                self._notify_missed_dose_during_security_alert(
                    medicine_name, reminder_data
                )
                deferred["alert_notified"] = True

    def _notify_missed_dose_during_security_alert(
        self, medicine_name: str, reminder_data: dict
    ):
        """Alert the caregiver that a scheduled dose was not taken because
        an active security alert prevented the reminder from being processed.

        This is separate from the normal missed-dose path so the reason
        ("security alert in progress") is clearly recorded.
        """
        station_id     = reminder_data.get("station_id", "unknown")
        scheduled_time = reminder_data.get("scheduled_time", "unknown")
        dosage_pills   = reminder_data.get("dosage_pills", 0)
        station_label  = station_id.replace("_", " ").title()

        if self.audio:
            self.audio.speak_async(
                f"Warning. {medicine_name} scheduled at {scheduled_time} "
                f"has not been taken. A security alert is still active on "
                f"{station_label}. Your caregiver has been notified."
            )

        self.telegram.send_missed_dose_alert(
            medicine_name,
            scheduled_time,
            self._security_alert_caregiver_timeout_minutes,
        )

        missed_event = {
            "timestamp":         time.time(),
            "expected_medicine": medicine_name,
            "expected_dosage":   dosage_pills,
            "result":            DecisionResult.NO_INTAKE,
            "verified":          False,
            "alerts": [
                {
                    "type":     "missed_dose_during_security_alert",
                    "severity": "critical",
                    "message":  (
                        f"Dose not taken — security alert active on {station_id}"
                    ),
                }
            ],
            "details": {
                "reason":     "security_alert_active",
                "station_id": station_id,
            },
            "scores": {},
        }
        self.database.log_medication_event(missed_event)

    # ------------------------------------------------------------------
    # MQTT / weight callbacks (arrive on MQTT thread)
    # ------------------------------------------------------------------

    def _on_weight_data(self, data: dict):
        if not hasattr(self, "weight_manager"):
            return
        self.weight_manager.process_weight_data(data)

    def _on_pill_removal(self, event_data: dict):
        pills_this_event = int(event_data.get("pills_removed", 0))
        self.logger.info(
            f"Pill removal detected: {pills_this_event} pill(s) "
            f"from {event_data['station_id']}"
        )
        if self.state_machine.get_state() != SystemState.REMINDER_ACTIVE:
            return
        station_id = event_data["station_id"]
        if (
            self.current_medication
            and self.current_medication.get("station_id") == station_id
        ):
            # Accumulate across multiple lift-and-replace events within the
            # same dose window so partial doses (e.g. 1 pill at a time) are
            # counted correctly toward the total required.
            prev = self._dose_pills_removed.get(station_id, 0)
            self._dose_pills_removed[station_id] = prev + pills_this_event
            self.logger.info(
                f"Dose window total for {station_id}: "
                f"{self._dose_pills_removed[station_id]} pill(s) "
                f"(+{pills_this_event} this event)"
            )
            event_data["queued_at"] = time.time()
            event_data["firmware_dosing_active"] = self._firmware_dosing_active
            self.pending_weight_event = event_data
            self.logger.info("Weight event queued for main-thread processing")

    def _on_bottle_lifted(self, event_data: dict):
        """
        Fired by WeightManager when the bottle is lifted off an armed station
        (WAITING_FOR_REMOVAL ? REMOVED transition).

        Scanning is already kept active during runtime, but we explicitly send
        start_scan here as a harmless reinforcement when a dosing interaction
        begins.
        """
        station_id = event_data.get("station_id", "unknown")
        self.logger.info(
            f"Bottle lifted from {station_id} - starting tag scanning"
        )
        self.tag_runtime_service.start_scanning(station_id)

    def _on_status_data(self, data: dict):
        """
        Callback for MQTT station status messages (arrives on MQTT thread).

        Routes ``dosing_complete`` events from the station firmware into
        the weight manager, which in turn fires ``_on_dosing_complete``.
        """
        status     = data.get("status", "")
        station_id = data.get("station_id", "")

        if status == "dosing_complete":
            self.logger.info(
                f"Firmware reports dosing_complete on {station_id}"
            )
            if hasattr(self, "weight_manager"):
                self.weight_manager.process_dosing_complete(data)
        elif status == "dosing_started":
            self.logger.info(
                f"Firmware confirmed dosing_started on {station_id}"
            )
        else:
            self.logger.debug(f"Station status: {station_id} -> {status}")

    def _on_dosing_complete(self, event_data: dict):
        """
        Fired by WeightManager.process_dosing_complete when the station
        firmware confirms that the correct number of pills has been
        removed from the bottle (while it remains on the scale).

        Sets the cumulative pill count and queues the event for the
        existing main-thread verification pipeline.
        """
        station_id    = event_data.get("station_id")
        pills_removed = int(event_data.get("pills_removed", 0))

        self.logger.info(
            f"Dosing complete callback: {pills_removed} pill(s) from {station_id}"
        )

        if self.state_machine.get_state() != SystemState.REMINDER_ACTIVE:
            self.logger.warning(
                "Dosing complete received but system is not in REMINDER_ACTIVE"
            )
            return

        if (
            not self.current_medication
            or self.current_medication.get("station_id") != station_id
        ):
            self.logger.warning(
                f"Dosing complete from {station_id} does not match "
                f"current medication station"
            )
            return

        # Record the firmware-counted pills for the cumulative dosage check.
        self._dose_pills_removed[station_id] = pills_removed
        self._firmware_dosing_active = False

        # Queue for the existing main-thread verification pipeline.
        event_data["queued_at"] = time.time()
        event_data["firmware_dosing_active"] = True
        self.pending_weight_event = event_data
        self.logger.info("Dosing complete event queued for verification")

    # ------------------------------------------------------------------
    # Main-thread event processing
    # ------------------------------------------------------------------

    def _process_pending_weight_event(self):
        if self.pending_weight_lock or not self.pending_weight_event:
            return
        event_data                = self.pending_weight_event
        self.pending_weight_event = None
        self.pending_weight_lock  = True
        paso_context = self._build_paso_context(event_data)
        try:
            processing_started_at = time.time()
            transport_start = event_data.get("published_at")
            transport_end = event_data.get("received_at", processing_started_at)

            if transport_start is not None:
                self._log_paso_stage_window(
                    paso_context,
                    "mqtt_transport",
                    float(transport_start),
                    float(transport_end),
                    notes={
                        "measured": True,
                        "mqtt_transport_ms": event_data.get("mqtt_transport_ms"),
                        "source": event_data.get("source"),
                    },
                )
            else:
                self._log_paso_stage_window(
                    paso_context,
                    "mqtt_transport",
                    float(transport_end),
                    float(transport_end),
                    notes={
                        "measured": False,
                        "reason": "station_publish_timestamp_unavailable",
                        "source": event_data.get("source"),
                    },
                )

            queue_start = float(
                event_data.get("queued_at")
                or event_data.get("received_at")
                or event_data.get("timestamp")
                or processing_started_at
            )
            queue_start = min(queue_start, processing_started_at)
            self._log_paso_stage_window(
                paso_context,
                "event_queueing",
                queue_start,
                processing_started_at,
                notes={
                    "event_type": event_data.get("event_type"),
                    "source": event_data.get("source"),
                    "firmware_dosing_active": event_data.get(
                        "firmware_dosing_active",
                        self._firmware_dosing_active,
                    ),
                },
            )

            self.state_machine.transition_to(
                SystemState.VERIFYING, {"event_data": event_data}
            )
            with self._profile_paso_stage(
                paso_context,
                "pipeline_total",
                notes=lambda: {
                    "event_type": event_data.get("event_type"),
                    "source": event_data.get("source"),
                    "firmware_dosing_active": event_data.get(
                        "firmware_dosing_active",
                        self._firmware_dosing_active,
                    ),
                    "final_outcome": paso_context.get("final_outcome"),
                    "verified": paso_context.get("verified"),
                },
            ):
                self._verify_medication_intake(event_data, paso_context=paso_context)
        finally:
            self.pending_weight_lock = False

    def _next_paso_run_id(self, station_id: str) -> str:
        self._paso_run_sequence += 1
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        return f"{stamp}_{station_id}_{self._paso_run_sequence:04d}"

    def _slugify(self, value: str) -> str:
        return "".join(
            ch.lower() if ch.isalnum() else "_"
            for ch in (value or "unknown")
        ).strip("_") or "unknown"

    def _build_paso_context(self, weight_event: dict) -> dict:
        station_id = (
            weight_event.get("station_id")
            or (self.current_medication or {}).get("station_id")
            or "unknown_station"
        )
        medicine_name = (
            (self.current_medication or {}).get("medicine_name")
            or weight_event.get("medicine_name")
            or "unknown_medicine"
        )
        source = weight_event.get("source") or weight_event.get("event_type") or "event"
        scenario = weight_event.get("scenario_name") or (
            f"{self._slugify(medicine_name)}_{self._slugify(source)}"
        )
        return {
            "run_id": self._next_paso_run_id(station_id),
            "scenario": scenario,
            "station_id": station_id,
            "medicine_name": medicine_name,
            "final_outcome": None,
            "verified": None,
            "decision_source": None,
        }

    def _profile_paso_stage(self, paso_context: dict, stage: str, notes=None):
        if not paso_context:
            return nullcontext()
        return profile_stage(
            self.profiler,
            paso_context["run_id"],
            paso_context["scenario"],
            paso_context["station_id"],
            stage,
            notes,
        )

    def _log_paso_stage_window(
        self,
        paso_context: dict,
        stage: str,
        start_ts: float,
        end_ts: float,
        notes=None,
    ):
        if not paso_context:
            return
        self.profiler.log_stage_window(
            run_id=paso_context["run_id"],
            scenario=paso_context["scenario"],
            station_id=paso_context["station_id"],
            stage=stage,
            start_ts=start_ts,
            end_ts=end_ts,
            notes=notes,
        )

    def _apply_runtime_profiler_context(self, paso_context: dict):
        if not paso_context:
            return
        self.scanner.set_profiler_context(
            self.profiler,
            paso_context["run_id"],
            paso_context["scenario"],
            paso_context["station_id"],
        )
        self.patient_monitor.set_profiler_context(
            self.profiler,
            paso_context["run_id"],
            paso_context["scenario"],
            paso_context["station_id"],
        )

    def _clear_runtime_profiler_context(self):
        self.scanner.clear_profiler_context()
        self.patient_monitor.clear_profiler_context()

    def _decision_result_value(self, decision: dict):
        if not decision:
            return None
        result = decision.get("result")
        if hasattr(result, "value"):
            return result.value
        return result

    def _generate_profiled_decision(
        self,
        paso_context: dict,
        decision_source: str,
        builder,
        notes=None,
    ) -> dict:
        decision = None
        resolved_notes = lambda: (
            notes() if callable(notes) else (notes or {})
        ) or {}

        with self._profile_paso_stage(
            paso_context,
            "decision_engine",
            notes=lambda: {
                **resolved_notes(),
                "decision_source": decision_source,
                "result": self._decision_result_value(decision),
                "verified": decision.get("verified") if decision else None,
            },
        ):
            decision = builder()

        paso_context["final_outcome"] = self._decision_result_value(decision)
        paso_context["verified"] = decision.get("verified") if decision else None
        paso_context["decision_source"] = decision_source
        return decision

    def _execute_output_and_logging(
        self,
        paso_context: dict,
        decision: dict,
        output_callable=None,
        medicine_name: str = None,
    ) -> bool:
        database_logged = False
        scheduler_marked = False

        with self._profile_paso_stage(
            paso_context,
            "output_logging",
            notes=lambda: {
                "result": self._decision_result_value(decision),
                "verified": decision.get("verified") if decision else None,
                "scheduler_marked": scheduler_marked,
                "database_logged": database_logged,
            },
        ):
            if output_callable:
                with self._profile_paso_stage(
                    paso_context,
                    "decision_output",
                    notes=lambda: {
                        "result": self._decision_result_value(decision),
                        "verified": decision.get("verified") if decision else None,
                    },
                ):
                    output_callable()

            if medicine_name and decision and decision.get("verified"):
                self.scheduler.mark_dose_taken(medicine_name)
                scheduler_marked = True

            with self._profile_paso_stage(
                paso_context,
                "database_logging",
                notes=lambda: {
                    "result": self._decision_result_value(decision),
                    "logged": database_logged,
                },
            ):
                database_logged = self.database.log_medication_event(decision)

        return database_logged

    def _render_pending_monitoring_ui(self):
        if not self.display or not self.pending_monitoring_ui:
            return
        elapsed, duration, message, swallow_count, expected_dosage = \
            self.pending_monitoring_ui
        self.display.show_monitoring_screen(
            elapsed, duration, message, swallow_count, expected_dosage
        )

    # ------------------------------------------------------------------
    # Reminder / missed-dose callbacks
    # ------------------------------------------------------------------

    def _recapture_fresh_baseline(self, station_id: str, timeout: float = 15.0) -> bool:
        """
        Wait for a genuinely stable weight reading and capture it as the fresh
        baseline for this dose window.  Uses the same settle-time logic as the
        pill-removal FSM so transient 'stable' blips from load-cell thermal
        creep are rejected.

        Blocks up to `timeout` seconds.  Returns True on success, False if the
        bottle is absent, the sensor is offline, or the timeout expires.
        """
        cfg         = self.weight_manager.station_configs.get(station_id, {})
        settle_time = float(cfg.get("event_settle_seconds", 1.5))
        medicine    = (
            self.current_medication.get("medicine_name", station_id)
            if self.current_medication else station_id
        )

        self.logger.info(
            f"[{station_id}] Recapturing fresh baseline "
            f"(settle={settle_time}s, timeout={timeout}s)..."
        )

        if self.display:
            self.display.show_pipeline_screen(
                "Calibrating Sensor",
                f"Re-calibrating weight sensor for {medicine}.\n"
                "Please keep the bottle still..."
            )

        deadline     = time.time() + timeout
        stable_since = None

        while time.time() < deadline:
            if not self.running:
                return False

            if self.display:
                self.display.update()

            status    = self.weight_manager.get_station_status(station_id)
            weight_g  = float(status.get("weight_g") or 0.0)
            is_stable = bool(status.get("stable", False))

            if not status.get("connected", False):
                stable_since = None
                time.sleep(0.2)
                continue

            if weight_g < self.min_secured_bottle_weight_g:
                stable_since = None
                time.sleep(0.2)
                continue

            if not is_stable:
                stable_since = None
                time.sleep(0.1)
                continue

            if stable_since is None:
                stable_since = time.time()

            if time.time() - stable_since < settle_time:
                time.sleep(0.1)
                continue

            ok = self.weight_manager.capture_current_baseline(station_id)
            if ok:
                self.logger.info(
                    f"[{station_id}] Fresh baseline captured: {weight_g:.2f}g "
                    f"(stable for ≥{settle_time:.1f}s)"
                )
                return True

            # MQTT race: weight_data changed between the poll and capture. Retry.
            stable_since = None
            time.sleep(0.1)

        self.logger.warning(
            f"[{station_id}] Baseline recapture timed out after {timeout:.0f}s – "
            "proceeding with last known baseline"
        )
        return False

    def _on_medication_reminder(self, reminder_data: dict):
        self.logger.info(f"Medication reminder triggered: {reminder_data}")
        self.current_medication = reminder_data
        station_id = reminder_data["station_id"]

        # Fresh dose window – clear any leftover state from a previous cycle.
        self._dose_pills_removed[station_id] = 0
        self._dose_attempt_count[station_id] = 0

        medicine_id = reminder_data.get("medicine_id") or \
            self._resolve_medicine_id_for_station(
                station_id, reminder_data.get("medicine_name")
            )

        if medicine_id:
            self.current_medication["medicine_id"] = medicine_id
            self.logger.info(f"Resolved medicine_id={medicine_id} for {station_id}")
        else:
            self.logger.warning(
                f"No registered medicine for {station_id}. "
                "Identity will fall back to QR/OCR."
            )

        self.state_machine.transition_to(SystemState.REMINDER_ACTIVE, reminder_data)

        medicine_name = reminder_data["medicine_name"]
        dosage        = reminder_data["dosage_pills"]
        time_str      = reminder_data["scheduled_time"]

        if self.display:
            self.display.show_reminder_screen(medicine_name, dosage, time_str)
        if self.audio:
            self.audio.announce_reminder(medicine_name, dosage)
        self.telegram.send_medication_reminder(medicine_name, dosage, time_str)

        # Recapture the baseline now (at scheduled dose time) so load-cell
        # drift since registration does not skew the pill-count calculation.
        # Mark as required first so _authorize falls back to single-sample
        # capture if the recapture times out or the bottle is absent.
        self.weight_manager.baseline_capture_required[station_id] = True
        recaptured = self._recapture_fresh_baseline(station_id)

        if recaptured and self.display:
            new_baseline_g = self.weight_manager.baseline_weights.get(station_id, 0.0)
            self.display.show_baseline_captured_screen(
                medicine_name, new_baseline_g, dosage, time_str
            )
            time.sleep(2.5)

        # Send dosing command to the station firmware.  The M5StickC will
        # guide the patient through pill removal (take more / put back)
        # and report dosing_complete when the correct count is confirmed.
        pill_weight_mg = self.weight_manager.get_pill_weight_mg(station_id)
        self.mqtt.send_start_dosing(station_id, dosage, pill_weight_mg)
        self._firmware_dosing_active = True
        self.logger.info(
            f"Sent start_dosing to {station_id}: "
            f"{dosage} pills @ {pill_weight_mg:.0f} mg each"
        )

        # Mark bottle as authorized so security system doesn't interfere.
        secure_state = self.secured_medications.get(station_id)
        if secure_state:
            secure_state["authorized"] = True

        # Start tag scanning so identity data is captured while the bottle
        # sits on the scale (tag faces the reader underneath).
        self.tag_runtime_service.start_scanning(station_id)

        if self.display:
            self.display.show_dosing_in_progress_screen(
                medicine_name, dosage, station_id
            )

    def _on_missed_dose(self, missed_data: dict):
        # If this medicine's reminder was deferred because of a security alert,
        # _process_deferred_reminders() owns caregiver notification — suppress
        # this callback to avoid double-alerting the caregiver.
        medicine_name = missed_data.get("medicine_name", "")
        if medicine_name in self._deferred_reminders:
            self.logger.info(
                f"Missed-dose callback for {medicine_name} suppressed — "
                "security alert is active; deferred-reminder system handles "
                "caregiver escalation."
            )
            return

        self.logger.warning(f"Missed dose: {missed_data}")

        # Cancel firmware dosing if still active.
        if self._firmware_dosing_active and self.current_medication:
            station_id = self.current_medication.get("station_id")
            if station_id:
                self.mqtt.send_stop_dosing(station_id)
            self._firmware_dosing_active = False

        self.telegram.send_missed_dose_alert(
            missed_data["medicine_name"],
            missed_data["scheduled_time"],
            missed_data["timeout_minutes"]
        )

        if self.display:
            self.display.show_warning_screen(
                "Missed Dose",
                f"{missed_data['medicine_name']} was not taken"
            )
        if self.audio:
            self.audio.announce_warning("Medication dose was missed")

        missed_event = {
            "timestamp":        time.time(),
            "expected_medicine": missed_data["medicine_name"],
            "expected_dosage":   0,
            "result":            DecisionResult.NO_INTAKE,
            "verified":          False,
            "alerts": [
                {
                    "type":     "missed_dose",
                    "severity": "critical",
                    "message":  "Dose not taken"
                }
            ],
            "details": {},
            "scores":  {}
        }
        self.database.log_medication_event(missed_event)
        self.state_machine.reset_to_idle()

        if self.current_medication:
            station_id = self.current_medication["station_id"]
            self.weight_manager.disable_event_detection(station_id)
            self.secured_medications.pop(station_id, None)

            self._enable_continuous_tag_scanning()

        self.current_medication    = None
        self.pending_monitoring_ui = None

        if self.display:
            self._show_idle_screen()

    # ------------------------------------------------------------------
    # Verification pipeline
    # ------------------------------------------------------------------

    def _verify_medication_intake(
        self,
        weight_event: dict,
        paso_context: dict = None,
    ):
        self.logger.info("Starting medication verification...")
        if not self.running:
            return
        paso_context = paso_context or self._build_paso_context(weight_event)
        self._apply_runtime_profiler_context(paso_context)

        medicine_name       = self.current_medication["medicine_name"]
        expected_dosage     = self.current_medication["dosage_pills"]
        expected_medicine_id = self.current_medication.get("medicine_id")
        expected_station_id  = self.current_medication["station_id"]

        if self.display:
            self.display.show_pipeline_screen(
                "Identity Check",
                f"Checking bottle identity for {medicine_name}"
            )

        weight_event_ts  = weight_event.get("timestamp", time.time())
        identity_cfg     = self.config.get("identity", {})
        tag_cfg          = identity_cfg.get("tag", {})
        integrated_mode  = tag_cfg.get("integrated_mode", True)
        coincident_window = tag_cfg.get("coincident_window_seconds", 15.0)

        identity_result = None
        ocr_result      = None

        with self._profile_paso_stage(
            paso_context,
            "identity_check_total",
            notes=lambda: {
                "method": identity_result.get("method") if identity_result else None,
                "success": identity_result.get("success") if identity_result else None,
                "reason": identity_result.get("reason") if identity_result else None,
                "integrated_mode": integrated_mode,
            },
        ):
            try:
                self.scanner.initialize_camera()

                with self._profile_paso_stage(
                    paso_context,
                    "tag_or_identity_check",
                    notes=lambda: {
                        "method": identity_result.get("method") if identity_result else None,
                        "success": identity_result.get("success") if identity_result else None,
                        "reason": identity_result.get("reason") if identity_result else None,
                        "integrated_mode": integrated_mode,
                    },
                ):
                    if integrated_mode:
                        identity_result = self.identity_manager.verify_identity_integrated(
                            expected_medicine_id=expected_medicine_id,
                            expected_medicine_name=medicine_name,
                            expected_station_id=expected_station_id,
                            weight_event_timestamp=weight_event_ts,
                            coincident_window_seconds=coincident_window
                        )
                    else:
                        identity_result = self.identity_manager.verify_identity(
                            expected_medicine_id=expected_medicine_id,
                            expected_medicine_name=medicine_name,
                            expected_station_id=expected_station_id
                        )

                self.logger.info(f"Identity result: {identity_result}")

                if identity_result.get("success"):
                    # If the verified tag carries a pill weight, keep the override
                    # up-to-date (e.g. after a system restart the override file may
                    # be stale or missing).
                    tag_record = identity_result.get("record") or {}
                    pill_weight_mg = tag_record.get("pill_weight_mg")
                    if pill_weight_mg is not None:
                        self.weight_manager.set_pill_weight_from_tag(
                            expected_station_id, pill_weight_mg
                        )

                    ocr_result = {
                        "success":       True,
                        "medicine_name": identity_result.get("medicine_name", medicine_name),
                        "confidence":    identity_result.get("confidence", 1.0),
                        "verified":      True,
                        "method":        identity_result.get("method")
                    }
                else:
                    ocr_result = None

            except Exception as e:
                self.logger.warning(f"Identity verification error: {e}")
                identity_result = {
                    "success":  False,
                    "method":   "none",
                    "verified": False,
                    "reason":   str(e)
                }
                ocr_result = None
            finally:
                self.scanner.release_camera()

        if not self.running:
            return

        if self.display:
            self.display.show_pipeline_screen(
                "Dosage Check",
                f"Checking pill count for {medicine_name}"
            )

        weight_result = None
        cumulative_weight_result = None
        cumulative_pills = 0
        cum_delta_err = 0.0
        tolerance_g = 0.0

        with self._profile_paso_stage(
            paso_context,
            "dosage_check",
            notes=lambda: {
                "expected_dosage": expected_dosage,
                "actual_dosage": cumulative_pills,
                "verified": (
                    cumulative_weight_result.get("verified")
                    if cumulative_weight_result else None
                ),
                "weight_delta_error_g": round(cum_delta_err, 3),
                "tolerance_g": round(tolerance_g, 3),
            },
        ):
            # Per-event weight check (used for the raw delta / pill_weight metadata).
            weight_result = self.weight_manager.verify_dosage(
                expected_station_id, expected_dosage
            )
            self.logger.info(f"Weight verification (per-event): {weight_result}")

            # Build a cumulative weight result that reflects all pills removed
            # during this dose window (across every lift-and-replace cycle).
            cumulative_pills = self._dose_pills_removed.get(expected_station_id, 0)
            pill_weight_g = float(
                weight_result.get("pill_weight_g")
                or self.weight_manager._get_pill_weight_g(expected_station_id)
            )
            cum_delta_g = cumulative_pills * pill_weight_g
            exp_delta_g = expected_dosage * pill_weight_g
            cum_delta_err = abs(cum_delta_g - exp_delta_g)
            tolerance_g = float(
                self.weight_manager.station_configs
                .get(expected_station_id, {})
                .get("dose_verification_tolerance_g", 0.12)
            )
            cumulative_weight_result = dict(weight_result)
            cumulative_weight_result.update({
                "actual":           cumulative_pills,
                "weight_change_g":  round(cum_delta_g,  3),
                "expected_delta_g": round(exp_delta_g,  3),
                "delta_error_g":    round(cum_delta_err, 3),
                "difference":       abs(cumulative_pills - expected_dosage),
                "verified":         (
                    cumulative_pills == expected_dosage
                    and cum_delta_err <= tolerance_g
                ),
                "status": (
                    "correct"
                    if cumulative_pills == expected_dosage and cum_delta_err <= tolerance_g
                    else "incorrect"
                ),
            })
            self.logger.info(
                f"Cumulative dosage check: {cumulative_pills}/{expected_dosage} pills "
                f"(delta_err={cum_delta_err:.3f}g, verified={cumulative_weight_result['verified']})"
            )

        # ------------------------------------------------------------------
        # HARD STOP: identity failed
        # ------------------------------------------------------------------
        if identity_result and not identity_result.get("success", False):
            self.logger.warning(
                "Stopping pipeline early due to identity mismatch/failure"
            )
            decision = self._generate_profiled_decision(
                paso_context,
                "decision_engine_verify_identity_failure",
                lambda: self.decision_engine.verify_medication_intake(
                    expected_medicine=medicine_name,
                    expected_dosage=expected_dosage,
                    identity_result=identity_result,
                    ocr_result=None,
                    weight_result=cumulative_weight_result,
                    monitoring_result=None
                ),
                notes=lambda: {
                    "identity_success": False,
                    "identity_method": identity_result.get("method"),
                },
            )
            self._execute_output_and_logging(
                paso_context,
                decision,
                output_callable=lambda: self._handle_decision(decision),
            )
            time.sleep(3)
            self._end_verification_cycle(expected_station_id)
            return

        # ------------------------------------------------------------------
        # DOSAGE CHECK with retry
        # ------------------------------------------------------------------
        if not cumulative_weight_result.get("verified", False):
            # ---- Overdose: too many pills physically removed – stop immediately ----
            if cumulative_pills > expected_dosage:
                self.logger.warning(
                    f"Overdose detected: {cumulative_pills} pills taken, "
                    f"{expected_dosage} required"
                )
                decision = self._generate_profiled_decision(
                    paso_context,
                    "decision_engine_verify_weight_overdose",
                    lambda: self.decision_engine.verify_medication_intake(
                        expected_medicine=medicine_name,
                        expected_dosage=expected_dosage,
                        identity_result=identity_result,
                        ocr_result=ocr_result,
                        weight_result=cumulative_weight_result,
                        monitoring_result=None
                    ),
                    notes=lambda: {
                        "actual_dosage": cumulative_pills,
                        "expected_dosage": expected_dosage,
                        "stage": "weight_overdose",
                    },
                )
                self._execute_output_and_logging(
                    paso_context,
                    decision,
                    output_callable=lambda: self._show_weight_overdose_feedback(
                        medicine_name,
                        cumulative_pills,
                        expected_dosage,
                    ),
                )
                time.sleep(5)
                self._end_verification_cycle(expected_station_id)
                return

            # ---- Under-dose: defer to intake phase – fall through to monitoring ----
            self.logger.info(
                f"Under-dose on weight ({cumulative_pills}/{expected_dosage}) "
                "- deferring dosage check to intake phase"
            )

        # Cumulative dosage is correct – proceed to patient monitoring.
        # Replace the per-event weight_result with the cumulative one so the
        # decision engine sees the accurate total.
        weight_result = cumulative_weight_result

        if not self.running:
            return

        # ------------------------------------------------------------------
        # Monitoring session (camera on for 30 s)
        # ------------------------------------------------------------------
        monitoring_result = None
        monitoring_attempt = 1
        with self._profile_paso_stage(
            paso_context,
            "monitoring_session",
            notes=lambda: {
                "attempt": monitoring_attempt,
                "compliance_status": (
                    monitoring_result.get("compliance_status")
                    if monitoring_result else None
                ),
                "swallow_count": (
                    monitoring_result.get("swallow_count")
                    if monitoring_result else None
                ),
                "processed_frame_count": (
                    monitoring_result.get("processed_frame_count")
                    if monitoring_result else None
                ),
                "avg_loop_ms": (
                    monitoring_result.get("avg_loop_ms")
                    if monitoring_result else None
                ),
            },
        ):
            monitoring_result = self._run_monitoring_session(
                expected_dosage,
                paso_context=paso_context,
            )
        total_swallows    = int(monitoring_result.get("swallow_count", 0))

        if not self.running:
            return

        # ---- Over-count ----
        if total_swallows > expected_dosage:
            self.logger.warning(
                f"Intake excess: {total_swallows} pill(s) consumed, "
                f"only {expected_dosage} expected for {medicine_name}"
            )
            decision = self._generate_profiled_decision(
                paso_context,
                "manual_incorrect_dosage_intake_monitoring",
                lambda: self._build_incorrect_dosage_decision(
                    medicine_name=medicine_name,
                    expected_dosage=expected_dosage,
                    actual_dosage=total_swallows,
                    stage="intake_monitoring"
                ),
                notes=lambda: {
                    "actual_dosage": total_swallows,
                    "expected_dosage": expected_dosage,
                },
            )
            self._execute_output_and_logging(
                paso_context,
                decision,
                output_callable=lambda: self._show_weight_overdose_feedback(
                    medicine_name,
                    total_swallows,
                    expected_dosage,
                ),
            )
            time.sleep(5)
            self._end_verification_cycle(expected_station_id)
            return

        # ---- Under-count: show mismatch screen then re-run monitoring ----
        if total_swallows < expected_dosage:
            remaining = expected_dosage - total_swallows
            pill_word = "pill" if remaining == 1 else "pills"
            self.logger.warning(
                f"Intake mismatch: {total_swallows} swallow(s) detected, "
                f"~{expected_dosage} expected for {medicine_name}"
            )
            with self._profile_paso_stage(
                paso_context,
                "decision_output",
                notes=lambda: {
                    "feedback": "incomplete_intake_retry_prompt",
                    "swallow_count": total_swallows,
                    "expected_dosage": expected_dosage,
                },
            ):
                if self.display:
                    self.display.show_intake_mismatch_screen(
                        medicine_name=medicine_name,
                        swallow_count=total_swallows,
                        expected_dosage=expected_dosage,
                    )
                if self.audio:
                    self.audio.announce_warning(
                        f"Only {total_swallows} swallow detected. "
                        f"Please take {remaining} more {pill_word}."
                    )
            time.sleep(3)

            # Run a second monitoring session (camera on, 30-second countdown)
            # identical in behaviour to the first monitoring phase.
            retry_result = None
            monitoring_attempt = 2
            with self._profile_paso_stage(
                paso_context,
                "monitoring_session",
                notes=lambda: {
                    "attempt": monitoring_attempt,
                    "compliance_status": (
                        retry_result.get("compliance_status")
                        if retry_result else None
                    ),
                    "swallow_count": (
                        retry_result.get("swallow_count")
                        if retry_result else None
                    ),
                    "processed_frame_count": (
                        retry_result.get("processed_frame_count")
                        if retry_result else None
                    ),
                    "avg_loop_ms": (
                        retry_result.get("avg_loop_ms")
                        if retry_result else None
                    ),
                },
            ):
                retry_result = self._run_monitoring_session(
                    expected_dosage,
                    paso_context=paso_context,
                )
            new_swallows   = int(retry_result.get("swallow_count", 0))
            total_swallows += new_swallows
            monitoring_result = retry_result
            self.logger.info(
                f"Incomplete-intake retry: +{new_swallows} swallows "
                f"(total {total_swallows}/{expected_dosage})"
            )

            if not self.running:
                return

            # Over-count during retry
            if total_swallows > expected_dosage:
                decision = self._generate_profiled_decision(
                    paso_context,
                    "manual_incorrect_dosage_incomplete_intake_over",
                    lambda: self._build_incorrect_dosage_decision(
                        medicine_name=medicine_name,
                        expected_dosage=expected_dosage,
                        actual_dosage=total_swallows,
                        stage="incomplete_intake"
                    ),
                    notes=lambda: {
                        "actual_dosage": total_swallows,
                        "expected_dosage": expected_dosage,
                    },
                )
                self._execute_output_and_logging(
                    paso_context,
                    decision,
                    output_callable=lambda: self._show_weight_overdose_feedback(
                        medicine_name,
                        total_swallows,
                        expected_dosage,
                    ),
                )
                time.sleep(5)
                self._end_verification_cycle(expected_station_id)
                return

            # Still under-count after retry → notify caregiver
            if total_swallows < expected_dosage:
                self.logger.warning("Incomplete intake after retry – notifying caregiver")
                decision = self._generate_profiled_decision(
                    paso_context,
                    "manual_incorrect_dosage_incomplete_intake_under",
                    lambda: self._build_incorrect_dosage_decision(
                        medicine_name=medicine_name,
                        expected_dosage=expected_dosage,
                        actual_dosage=total_swallows,
                        stage="incomplete_intake"
                    ),
                    notes=lambda: {
                        "actual_dosage": total_swallows,
                        "expected_dosage": expected_dosage,
                    },
                )
                self._execute_output_and_logging(
                    paso_context,
                    decision,
                    output_callable=lambda: self._show_incomplete_intake_feedback(
                        medicine_name,
                        total_swallows,
                        expected_dosage,
                    ),
                )
                time.sleep(5)
                self._end_verification_cycle(expected_station_id)
                return

        # Propagate the final swallow count so the decision engine and
        # database record see the accurate total.
        monitoring_result["swallow_count"] = total_swallows

        if not self.running:
            return

        decision = self._generate_profiled_decision(
            paso_context,
            "decision_engine_verify_final",
            lambda: self.decision_engine.verify_medication_intake(
                expected_medicine=medicine_name,
                expected_dosage=expected_dosage,
                identity_result=identity_result,
                ocr_result=ocr_result,
                weight_result=weight_result,
                monitoring_result=monitoring_result
            ),
            notes=lambda: {
                "swallow_count": total_swallows,
                "expected_dosage": expected_dosage,
            },
        )

        self._execute_output_and_logging(
            paso_context,
            decision,
            output_callable=lambda: self._handle_decision(decision),
            medicine_name=medicine_name,
        )

        time.sleep(3)
        self._end_verification_cycle(expected_station_id)

    def _show_weight_overdose_feedback(
        self,
        medicine_name: str,
        actual_dosage: int,
        expected_dosage: int,
    ):
        if self.display:
            self.display.show_overdose_screen(
                medicine_name, actual_dosage, expected_dosage
            )
        if self.audio:
            self.audio.announce_warning(
                f"Too many pills detected. "
                f"You took {actual_dosage} but only {expected_dosage} "
                f"are required. Please contact your caregiver."
            )
        self.telegram.send_incorrect_dosage_alert(
            medicine_name=medicine_name,
            expected=expected_dosage,
            actual=actual_dosage,
        )

    def _show_incomplete_intake_feedback(
        self,
        medicine_name: str,
        actual_dosage: int,
        expected_dosage: int,
    ):
        if self.display:
            self.display.show_caregiver_notification_screen(
                medicine_name=medicine_name,
                swallow_count=actual_dosage,
                expected_dosage=expected_dosage,
            )
        if self.audio:
            self.audio.announce_warning(
                "Time is up. Your caregiver has been notified."
            )
        self.telegram.send_incorrect_dosage_alert(
            medicine_name=medicine_name,
            expected=expected_dosage,
            actual=actual_dosage,
        )

    def _build_incorrect_dosage_decision(
        self,
        medicine_name: str,
        expected_dosage: int,
        actual_dosage: int,
        stage: str,
    ) -> dict:
        """Create a consistent incorrect-dosage event for early-stop paths."""
        return {
            "timestamp":        time.time(),
            "expected_medicine": medicine_name,
            "expected_dosage":   expected_dosage,
            "result":            DecisionResult.INCORRECT_DOSAGE,
            "verified":          False,
            "alerts": [
                {
                    "type":     "incorrect_dosage",
                    "severity": "critical",
                    "message": (
                        f"Incorrect dosage: expected {expected_dosage}, "
                        f"detected {actual_dosage}"
                    ),
                }
            ],
            "details": {
                "weight_expected": expected_dosage,
                "weight_actual":   actual_dosage,
                "actual_dosage":   actual_dosage,
                "dose_error_stage": stage,
            },
            "scores": {
                "weight": 0.0,
            },
        }

    def _run_monitoring_session(
        self,
        expected_dosage: int,
        paso_context: dict = None,
    ) -> dict:
        """
        Run a single 30-second patient monitoring session and return the
        results dict.  Handles display updates, state transition, and all
        error cases.  Safe to call multiple times within one intake cycle.
        """
        self.logger.info("Starting patient monitoring (30 seconds)...")
        self.state_machine.transition_to(SystemState.MONITORING_PATIENT)
        self.pending_monitoring_ui = (0, 30, "Monitoring intake...", 0, expected_dosage)

        if self.display:
            self.display.show_monitoring_screen(
                0, 30, "Monitoring intake...", 0, expected_dosage
            )

        try:
            def progress_callback(detections, elapsed, duration):
                live_count = int(detections.get("swallow_count", 0))
                self.pending_monitoring_ui = (
                    elapsed, duration, "Monitoring intake...",
                    live_count, expected_dosage
                )

            started = self.patient_monitor.start_monitoring(
                duration=30, callback=progress_callback
            )

            if not started:
                self.logger.warning("Patient monitoring could not start")
                return {
                    "compliance_status": "no_intake",
                    "swallow_count":     0,
                    "cough_count":       0,
                    "hand_motion_count": 0,
                    "processed_frame_count": 0,
                    "monitoring_duration_s": 0.0,
                    "avg_loop_ms": 0.0,
                    "peak_loop_ms": 0.0,
                }

            while self.patient_monitor.is_monitoring_active():
                if not self.running:
                    self.patient_monitor.cleanup()
                    return {
                        "compliance_status": "unclear",
                        "swallow_count":     0,
                        "cough_count":       0,
                        "hand_motion_count": 0,
                        "processed_frame_count": 0,
                        "monitoring_duration_s": 0.0,
                        "avg_loop_ms": 0.0,
                        "peak_loop_ms": 0.0,
                    }
                if self.display:
                    self._render_pending_monitoring_ui()
                    self.display.update()
                time.sleep(0.1)

            result = self.patient_monitor.get_results()
            self.logger.info(
                f"Monitoring complete: status={result['compliance_status']} "
                f"swallows={result.get('swallow_count', 0)}"
            )
            return result

        except Exception as e:
            self.logger.error(f"Patient monitoring failed: {e}")
            return {
                "compliance_status": "unclear",
                "swallow_count":     0,
                "cough_count":       0,
                "hand_motion_count": 0,
                "processed_frame_count": 0,
                "monitoring_duration_s": 0.0,
                "avg_loop_ms": 0.0,
                "peak_loop_ms": 0.0,
            }

    def _wait_for_pill_removal_event(self, timeout_seconds: float = 120.0):
        """
        Block until the weight sensor fires a pill-removal event or the
        timeout expires.  The caller must have already transitioned to
        REMINDER_ACTIVE so that _on_pill_removal() queues the event.

        Returns the event dict on success, or None on timeout / shutdown.
        """
        self.pending_weight_event = None
        deadline = time.time() + timeout_seconds

        while time.time() < deadline:
            if not self.running:
                return None
            if self.pending_weight_event:
                event = self.pending_weight_event
                self.pending_weight_event = None
                return event
            if self.display:
                self.display.update()
            time.sleep(0.05)

        return None

    def _end_verification_cycle(self, station_id: str):
        """
        Common teardown after every verification path (success, early-exit,
        missed dose).  Disarms the weight sensor, clears medication state,
        and keeps continuous tag scanning enabled for normal runtime.
        """
        # Cancel firmware dosing if still active.
        if self._firmware_dosing_active:
            self.mqtt.send_stop_dosing(station_id)
            self._firmware_dosing_active = False

        self.state_machine.reset_to_idle()
        self.weight_manager.disable_event_detection(station_id)
        self.secured_medications.pop(station_id, None)
        self.current_medication    = None
        self.pending_monitoring_ui = None
        self._last_security_violation_message = None

        # Clear per-dose retry counters for this station.
        self._dose_pills_removed.pop(station_id, None)
        self._dose_attempt_count.pop(station_id, None)

        self._enable_continuous_tag_scanning()
        self._clear_runtime_profiler_context()

        if self.display:
            self._show_idle_screen()

    # ------------------------------------------------------------------
    # Decision handling
    # ------------------------------------------------------------------

    def _handle_decision(self, decision: dict):
        result   = decision["result"]
        messages = self.decision_engine.get_alert_messages(decision)

        if result.value == "success":
            if self.display:
                self.display.show_success_screen(
                    "Correct medicine and dosage",
                    messages["patient_message"]
                )
            if self.audio:
                self.audio.announce_success(decision.get("expected_medicine", "medication"))

        elif result.value == "wrong_medicine":
            if self.display:
                self.display.show_warning_screen(
                    "Wrong medicine detected",
                    messages["patient_message"]
                )
            if self.audio:
                self.audio.announce_warning(messages["patient_message"])

        elif result.value == "incorrect_dosage":
            if self.display:
                self.display.show_warning_screen(
                    "Incorrect dosage detected",
                    messages["patient_message"]
                )
            if self.audio:
                self.audio.announce_warning(messages["patient_message"])

        elif result.value == "no_intake":
            if self.display:
                self.display.show_warning_screen(
                    "No intake detected",
                    messages["patient_message"]
                )
            if self.audio:
                self.audio.announce_warning(messages["patient_message"])

        else:
            if self.display:
                self.display.show_warning_screen(
                    "Verification needs attention",
                    messages["patient_message"]
                )
            if self.audio:
                self.audio.announce_warning(messages["patient_message"])

    # ------------------------------------------------------------------
    # Signal handler
    # ------------------------------------------------------------------

    def _signal_handler(self, signum, frame):
        self.logger.info("Shutdown signal received")
        self.stop()

    # ------------------------------------------------------------------
    # Schedule helpers
    # ------------------------------------------------------------------

    def _build_scheduler_entries_from_database(self, registered: list) -> list:
        entries = []

        for record in registered:
            medicine_name = record.get("medicine_name")
            station_id    = record.get("station_id")
            dosage        = record.get("dosage_amount", 1)
            time_slots    = record.get("time_slots", "")

            if not medicine_name or not time_slots:
                self.logger.warning(
                    f"Skipping incomplete record: {record.get('medicine_id')}"
                )
                continue

            times = [t.strip() for t in time_slots.split(",") if t.strip()]
            if not times:
                continue

            entries.append({
                "name":         medicine_name,
                "station_id":   station_id,
                "dosage_pills": dosage,
                "times":        times,
            })

        return entries

    def _load_schedule_from_database(self):
        registered = self.database.list_registered_medicines()
        if not registered:
            self.logger.warning("No registered medicines found in database")
            return

        entries = self._build_scheduler_entries_from_database(registered)
        if not entries:
            self.logger.warning("No usable medication schedules found in database")
            return

        # The database is the source of truth after onboarding, so replace
        # any placeholder config schedule before the scheduler starts.
        self.scheduler.medications = entries

        for entry in entries:
            self.logger.info(
                f"Loaded from DB into scheduler: {entry['name']} "
                f"at {entry['times']} on {entry['station_id']}"
            )

        self.logger.info(
            f"Scheduler loaded "
            f"{len(self.scheduler.get_scheduled_medicines())} "
            f"medicine(s) from database"
        )

    def _build_schedule_summary(self, medicines: list) -> list:
        entries = []
        for m in medicines:
            name       = m.get("medicine_name", "Unknown")
            dosage     = m.get("dosage_amount", "?")
            time_slots = m.get("time_slots", "")
            for t in time_slots.split(","):
                t = t.strip()
                if t:
                    entries.append(f"{t} - {name} ({dosage} pill(s))")
        entries.sort()
        return entries

    def _get_idle_screen_payload(self):
        next_medication = None
        today_schedule = []

        if hasattr(self, "scheduler") and self.scheduler:
            next_medication = self.scheduler.get_next_scheduled_time()
        if hasattr(self, "database") and self.database:
            today_schedule = self._build_schedule_summary(
                self.database.list_registered_medicines()
            )

        return {
            "next_medication": next_medication,
            "today_schedule": today_schedule,
        }

    def _show_idle_screen(self):
        if self.display:
            self.display.show_idle_screen(self._get_idle_screen_payload())

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    def start(self):
        self.logger.info("Starting medication system...")
        self.running = True

        EXPECTED_MEDICINE_COUNT = 1  # one medicine per station
        STATION_IDS = list(self.weight_manager.station_configs.keys())
 
        onboarding_was_needed = any(
            not self._station_has_existing_schedule(sid) for sid in STATION_IDS
        )
 
        # Determine which stations actually need onboarding so we can give the
        # patient an accurate "Station X of Y" progress count.
        stations_to_onboard = [
            sid for sid in STATION_IDS
            if not self._station_has_existing_schedule(sid)
        ]
        station_total = len(stations_to_onboard)
 
        all_registered = True
        station_number = 0
        for station_id in STATION_IDS:
            if self._station_has_existing_schedule(station_id):
                self.logger.info(
                    f"Schedule already in place for {station_id}, skipping onboarding"
                )
                continue
            station_number += 1
            ok = self.registration_manager.run_onboarding_if_needed(
                station_id=station_id,
                expected_medicine_count=EXPECTED_MEDICINE_COUNT,
                scheduler=self.scheduler,
                station_number=station_number,
                station_total=station_total,
            )
            if not ok:
                self.logger.error(
                    f"Onboarding did not complete for {station_id}. "
                    "System may have partial setup."
                )
                all_registered = False
        self._enable_continuous_tag_scanning()

        self._load_schedule_from_database()

        if onboarding_was_needed and all_registered:
            registered_medicines = self.database.list_registered_medicines()
            if registered_medicines:
                schedule_summary = self._build_schedule_summary(registered_medicines)
                self.telegram.send_onboarding_complete(
                    medicines=registered_medicines,
                    schedule_summary=schedule_summary
                )

        self.scheduler.start()
        self._bootstrap_registered_station_security_state()

        if self.display:
            self._show_idle_screen()
            if self._has_pending_security_violation():
                self._refresh_security_violation_screen()

        self.logger.info("System ready")

        try:
            while self.running:
                self._process_secured_bottle_placements()
                self._audit_occupied_stations_with_nfc()
                self._process_secured_bottle_movements()
                self._process_deferred_reminders()
                self._process_pending_manual_reminder()
                self._authorize_current_medication_if_ready()
                self._process_pending_weight_event()
                if self.display:
                    self.display.update()
                    if self.state_machine.get_state() == SystemState.IDLE:
                        current_minute = datetime.now().strftime('%H:%M')
                        if current_minute != self._last_idle_minute:
                            self._last_idle_minute = current_minute
                            # Only refresh to idle when there is no active
                            # security alert being displayed.  The alert screen
                            # stays visible until the issue is resolved; it is
                            # re-evaluated every tick by
                            # _process_secured_bottle_movements →
                            # _refresh_security_violation_screen, which clears
                            # _last_security_violation_message and calls
                            # _show_idle_screen when violations are gone.
                            if not self._has_pending_security_violation():
                                self._show_idle_screen()
                time.sleep(0.1)

        except KeyboardInterrupt:
            self.logger.info("Keyboard interrupt received")
        except Exception as e:
            self.logger.critical(f"Fatal error in main loop: {e}")
            if self.display:
                self.display.show_error_screen(str(e))
        finally:
            self.stop()

    def stop(self):
        if self._stop_called:
            return
        self._stop_called = True
        self.logger.info("Stopping medication system...")
        self.running = False

        try:
            if hasattr(self, "scheduler") and self.scheduler:
                self.scheduler.stop()
            if hasattr(self, "patient_monitor") and self.patient_monitor:
                self.patient_monitor.cleanup()
            if hasattr(self, "telegram") and self.telegram:
                self.telegram.stop_queue_processor()
            if hasattr(self, "mqtt") and self.mqtt:
                self.mqtt.disconnect()
            if self.display:
                self.display.cleanup()
            if self.audio:
                self.audio.cleanup()
            if hasattr(self, "tag_runtime_service") and self.tag_runtime_service:
                # Ensure scanner is stopped cleanly on shutdown
                self.tag_runtime_service.stop_scanning()
                self.tag_runtime_service.stop()
            if hasattr(self, "database") and self.database:
                self.database.cleanup()
            self.logger.info("System stopped gracefully")
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}")


def main():
    config_path = Path("config/config.yaml")
    if not config_path.exists():
        print("ERROR: config/config.yaml not found.")
        print(
            "Copy config/config.example.yaml to config/config.yaml "
            "and fill in your values."
        )
        sys.exit(1)

    try:
        system = MedicationSystem(
            config_path=str(config_path),
            enable_display=True,
            enable_audio=True
        )
        system.start()
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
