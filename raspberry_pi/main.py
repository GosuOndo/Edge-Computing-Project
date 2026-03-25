#!/usr/bin/env python3
"""
Smart Medication System - Main Application

Edge-based medication verification system with real-time monitoring.
Orchestrates all modules and handles the complete medication intake workflow.

Tag scan control
-----------------
Scanning is gated by the firmware.  The Pi controls the gate via MQTT:

  start_scanning()  - sent when the bottle is lifted (bottle_lifted_callback)
                      so the reader is active and ready to capture the tag
                      as the bottle is placed back on the scale.

  stop_scanning()   - sent after every verification cycle (success, failure,
                      or missed dose) so stale scans do not accumulate
                      between dose windows.

Onboarding scanning is controlled entirely by RegistrationManager.
"""

import sys
import time
import signal
from datetime import datetime, timedelta
from pathlib import Path

import os
os.environ["SDL_VIDEO_FBDEV"] = "/dev/fb0"

sys.path.insert(0, str(Path(__file__).parent))

from utils.logger import get_logger
from utils.config_loader import get_config

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

        self.enable_display = enable_display
        self.enable_audio   = enable_audio
        self.running        = False
        self._stop_called   = False

        self.state_machine    = StateMachine(self.logger)
        self.current_medication = None

        self.pending_weight_event        = None
        self.pending_weight_lock         = False
        self.pending_manual_reminder     = None
        self.pending_manual_reminder_lock = False
        self.pending_monitoring_ui       = None
        self.secured_medications         = {}
        self._processed_tag_scans        = {}
        self.min_secured_bottle_weight_g = float(
            self.config.get("registration", {}).get("min_bottle_weight_g", 5.0)
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
            self.mqtt.connect()

            self.weight_manager = WeightManager(
                self.config["weight_sensors"], self.logger
            )
            self.weight_manager.set_pill_removal_callback(self._on_pill_removal)
            # NEW: start scanning when the bottle is lifted so the reader
            # is ready to capture the tag when the bottle is placed back.
            self.weight_manager.set_bottle_lifted_callback(self._on_bottle_lifted)

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
                self.display.show_idle_screen()
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
            # NEW: command_topic tells the firmware to start/stop scanning.
            # Defaults to tag_reader_1; override in config.yaml if needed:
            #   identity.tag.command_topic: "medication/tag/command/tag_reader_1"
            command_topic = tag_cfg.get(
                "command_topic", "medication/tag/command/tag_reader_1"
            )

            self.tag_runtime_service = TagRuntimeService(
                mqtt_config=self.config["mqtt"],
                database=self.database,
                logger=self.logger,
                topic=tag_topic,
                command_topic=command_topic,   # NEW
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

            self.scheduler = MedicationScheduler(
                self.config["schedule"], self.logger
            )
            self.scheduler.set_reminder_callback(self._on_medication_reminder)
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
        previous_secure_state = self.secured_medications.get(station_id)
        was_resecured = bool(
            previous_secure_state
            and previous_secure_state.get("early_alert_sent", False)
        )
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

        if was_resecured:
            if getattr(self, "tag_runtime_service", None):
                self.tag_runtime_service.stop_scanning()
                self.tag_runtime_service.clear_latest_scan()

            if getattr(self, "display", None):
                next_scheduled = None
                if hasattr(self, "scheduler") and self.scheduler:
                    next_scheduled = self.scheduler.get_next_scheduled_time()
                self.display.show_idle_screen(next_scheduled)

    def _process_secured_bottle_placements(self):
        latest = self.tag_runtime_service.get_latest_scan()
        if not latest:
            return

        scan_received_at = float(latest.get("received_at", 0.0))
        scan_msg         = latest.get("scan_msg") or {}
        record           = self._resolve_record_from_scan(scan_msg)
        if not record:
            return

        station_id = self._get_station_waiting_for_resecure() or record.get("station_id")
        if not station_id:
            return

        if scan_received_at <= self._processed_tag_scans.get(station_id, 0.0):
            return

        if (
            self.current_medication
            and self.current_medication.get("station_id") == station_id
        ):
            return

        record_ok, mismatch_reason = self._validate_secured_bottle_record(
            station_id, record
        )
        if not record_ok:
            self._processed_tag_scans[station_id] = scan_received_at
            self.logger.warning(
                f"Rejected bottle re-secure for {station_id}: {mismatch_reason}"
            )
            self._prompt_return_bottle_to_station(
                title="Wrong bottle detected",
                message="Please place the correct medicine back onto the station"
            )
            return

        status = self.weight_manager.get_station_status(station_id)
        if not status.get("connected"):
            return

        weight_g = float(status.get("weight_g") or 0.0)
        if not status.get("stable", False):
            return
        if weight_g < self.min_secured_bottle_weight_g:
            return

        secure_record = dict(record)
        secure_record["station_id"] = station_id
        self._secure_bottle_until_due(secure_record, scan_received_at, weight_g)

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

    def _get_station_waiting_for_resecure(self):
        for station_id, secure_state in self.secured_medications.items():
            if secure_state.get("authorized", False):
                continue
            if not secure_state.get("early_alert_sent", False):
                continue
            if (
                self.current_medication
                and self.current_medication.get("station_id") == station_id
            ):
                continue

            status = self.weight_manager.get_station_status(station_id)
            if not status.get("connected"):
                continue
            if not status.get("stable", False):
                continue

            weight_g = float(status.get("weight_g") or 0.0)
            if weight_g < self.min_secured_bottle_weight_g:
                continue

            return station_id

        return None

    def _validate_secured_bottle_record(self, station_id: str, record: dict):
        expected = self.database.get_registered_medicine_by_station(station_id)
        if not expected:
            expected = self.secured_medications.get(station_id, {})

        compared = False
        comparisons = [
            ("tag_uid", "tag UID"),
            ("medicine_id", "medicine ID"),
            ("medicine_name", "medicine name"),
        ]

        for field, label in comparisons:
            expected_value = expected.get(field)
            actual_value   = record.get(field)
            if not expected_value or not actual_value:
                continue

            compared = True
            if field == "medicine_name":
                expected_value = str(expected_value).strip().upper()
                actual_value   = str(actual_value).strip().upper()

            if expected_value != actual_value:
                return False, (
                    f"{label} mismatch: expected {expected.get(field)}, "
                    f"got {record.get(field)}"
                )

        if expected.get("station_id") and record.get("station_id"):
            compared = True
            if expected["station_id"] != record["station_id"]:
                return False, (
                    f"station mismatch: expected {expected['station_id']}, "
                    f"got {record['station_id']}"
                )

        if compared:
            return True, None

        return False, "unable to confirm bottle identity from scan"

    def _prompt_return_bottle_to_station(
        self,
        title: str = "Bottle removed too early",
        message: str = "Please place the medicine back onto the station"
    ):

        self.logger.info(f"Prompting patient: {message}")

        if getattr(self, "display", None):
            self.display.show_warning_screen(
                title,
                message
            )

        if getattr(self, "audio", None):
            self.audio.speak_async(message)

    def _start_resecure_scan(self):
        if not getattr(self, "tag_runtime_service", None):
            return

        self.tag_runtime_service.clear_latest_scan()
        self.tag_runtime_service.start_scanning()

    def _process_secured_bottle_movements(self):
        now_ts = time.time()

        for station_id, secure_state in self.secured_medications.items():
            if now_ts >= secure_state.get("next_due_timestamp", 0):
                continue

            if secure_state.get("authorized", False):
                continue

            if (
                self.current_medication
                and self.current_medication.get("station_id") == station_id
            ):
                continue

            status   = self.weight_manager.get_station_status(station_id)
            weight_g = float(status.get("weight_g") or 0.0)
            bottle_present = (
                bool(status.get("connected"))
                and weight_g >= self.min_secured_bottle_weight_g
            )

            if bottle_present:
                secure_state["present"] = True
                if status.get("stable", False):
                    secure_state["current_weight_g"] = weight_g
                continue

            if secure_state.get("present", False) and not secure_state.get(
                "early_alert_sent", False
            ):
                self._notify_unauthorized_bottle_movement(secure_state)
                self._prompt_return_bottle_to_station()
                self._start_resecure_scan()
                secure_state["early_alert_sent"] = True

            secure_state["present"] = False

    def _authorize_current_medication_if_ready(self):
        if not self.current_medication:
            return False

        if self.state_machine.get_state() != SystemState.REMINDER_ACTIVE:
            return False

        station_id = self.current_medication["station_id"]
        status     = self.weight_manager.get_station_status(station_id)
        if status.get("event_detection_enabled"):
            return True
        if not status.get("connected"):
            return False

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
            f"Authorized bottle removal for {station_id} at scheduled time "
            f"with baseline={self.weight_manager.baseline_weights.get(station_id):.2f}g"
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
        reminder_data                = self.pending_manual_reminder
        self.pending_manual_reminder = None
        self.pending_manual_reminder_lock = True
        try:
            self._on_medication_reminder(reminder_data)
        finally:
            self.pending_manual_reminder_lock = False

    # ------------------------------------------------------------------
    # MQTT / weight callbacks (arrive on MQTT thread)
    # ------------------------------------------------------------------

    def _on_weight_data(self, data: dict):
        if not hasattr(self, "weight_manager"):
            return
        self.weight_manager.process_weight_data(data)

    def _on_pill_removal(self, event_data: dict):
        self.logger.info(
            f"Pill removal detected: {event_data['pills_removed']} pill(s) "
            f"from {event_data['station_id']}"
        )
        if self.state_machine.get_state() != SystemState.REMINDER_ACTIVE:
            return
        station_id = event_data["station_id"]
        if (
            self.current_medication
            and self.current_medication.get("station_id") == station_id
        ):
            self.pending_weight_event = event_data
            self.logger.info("Weight event queued for main-thread processing")

    def _on_bottle_lifted(self, event_data: dict):
        """
        Fired by WeightManager when the bottle is lifted off an armed station
        (WAITING_FOR_REMOVAL ? REMOVED transition).

        We start scanning immediately so the reader is active and ready to
        capture the tag the moment the bottle is placed back on the scale.
        This is the only time scanning is enabled outside of onboarding.
        """
        station_id = event_data.get("station_id", "unknown")
        self.logger.info(
            f"Bottle lifted from {station_id} - starting tag scanning"
        )
        self.tag_runtime_service.start_scanning()

    # ------------------------------------------------------------------
    # Main-thread event processing
    # ------------------------------------------------------------------

    def _process_pending_weight_event(self):
        if self.pending_weight_lock or not self.pending_weight_event:
            return
        event_data                = self.pending_weight_event
        self.pending_weight_event = None
        self.pending_weight_lock  = True
        try:
            self.state_machine.transition_to(
                SystemState.VERIFYING, {"event_data": event_data}
            )
            self._verify_medication_intake(event_data)
        finally:
            self.pending_weight_lock = False

    def _render_pending_monitoring_ui(self):
        if not self.display or not self.pending_monitoring_ui:
            return
        elapsed, duration, message = self.pending_monitoring_ui
        self.display.show_monitoring_screen(elapsed, duration, message)

    # ------------------------------------------------------------------
    # Reminder / missed-dose callbacks
    # ------------------------------------------------------------------

    def _on_medication_reminder(self, reminder_data: dict):
        self.logger.info(f"Medication reminder triggered: {reminder_data}")
        self.current_medication = reminder_data
        station_id = reminder_data["station_id"]

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
        self._authorize_current_medication_if_ready()

    def _on_missed_dose(self, missed_data: dict):
        self.logger.warning(f"Missed dose: {missed_data}")

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

            # Stop scanning - no verification will happen for this dose window
            self.tag_runtime_service.stop_scanning()
            self.logger.info(
                f"Tag scanning STOPPED after missed dose on {station_id}"
            )

        self.current_medication    = None
        self.pending_monitoring_ui = None

        if self.display:
            self.display.show_idle_screen(self.scheduler.get_next_scheduled_time())

    # ------------------------------------------------------------------
    # Verification pipeline
    # ------------------------------------------------------------------

    def _verify_medication_intake(self, weight_event: dict):
        self.logger.info("Starting medication verification...")
        if not self.running:
            return

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

        try:
            self.scanner.initialize_camera()

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

        weight_result = self.weight_manager.verify_dosage(
            expected_station_id, expected_dosage
        )
        self.logger.info(f"Weight verification: {weight_result}")

        # HARD STOP on identity failure
        if identity_result and not identity_result.get("success", False):
            self.logger.warning(
                "Stopping pipeline early due to identity mismatch/failure"
            )
            decision = self.decision_engine.verify_medication_intake(
                expected_medicine=medicine_name,
                expected_dosage=expected_dosage,
                identity_result=identity_result,
                ocr_result=None,
                weight_result=weight_result,
                monitoring_result=None
            )
            self._handle_decision(decision)
            self.database.log_medication_event(decision)

            time.sleep(3)
            self._end_verification_cycle(expected_station_id)
            return

        # HARD STOP on wrong dosage
        if not weight_result.get("verified", False):
            self.logger.warning(
                "Stopping pipeline early due to incorrect dosage"
            )
            decision = self.decision_engine.verify_medication_intake(
                expected_medicine=medicine_name,
                expected_dosage=expected_dosage,
                identity_result=identity_result,
                ocr_result=ocr_result,
                weight_result=weight_result,
                monitoring_result=None
            )
            self._handle_decision(decision)
            self.database.log_medication_event(decision)

            time.sleep(3)
            self._end_verification_cycle(expected_station_id)
            return

        if not self.running:
            return

        self.logger.info("Starting patient monitoring (30 seconds)...")
        self.state_machine.transition_to(SystemState.MONITORING_PATIENT)
        self.pending_monitoring_ui = (0, 30, "Monitoring intake...")
        monitoring_result = None

        if self.display:
            self.display.show_pipeline_screen(
                "Patient Monitoring",
                "Please bring hand to mouth and swallow naturally"
            )

        try:
            def progress_callback(detections, elapsed, duration):
                self.pending_monitoring_ui = (elapsed, duration, "Monitoring intake...")

            started = self.patient_monitor.start_monitoring(
                duration=30, callback=progress_callback
            )

            if not started:
                self.logger.warning("Patient monitoring could not start")
                monitoring_result = {
                    "compliance_status": "no_intake",
                    "swallow_count":     0,
                    "cough_count":       0,
                    "hand_motion_count": 0
                }
            else:
                while self.patient_monitor.is_monitoring_active():
                    if not self.running:
                        self.patient_monitor.cleanup()
                        return
                    if self.display:
                        self._render_pending_monitoring_ui()
                        self.display.update()
                    time.sleep(0.1)

                monitoring_result = self.patient_monitor.get_results()
                self.logger.info(
                    f"Monitoring complete: {monitoring_result['compliance_status']}"
                )

        except Exception as e:
            self.logger.error(f"Patient monitoring failed: {e}")
            monitoring_result = {
                "compliance_status": "unclear",
                "swallow_count":     0,
                "cough_count":       0,
                "hand_motion_count": 0
            }

        if not self.running:
            return

        decision = self.decision_engine.verify_medication_intake(
            expected_medicine=medicine_name,
            expected_dosage=expected_dosage,
            identity_result=identity_result,
            ocr_result=ocr_result,
            weight_result=weight_result,
            monitoring_result=monitoring_result
        )

        self._handle_decision(decision)

        if decision["verified"]:
            self.scheduler.mark_dose_taken(medicine_name)

        self.database.log_medication_event(decision)

        time.sleep(3)
        self._end_verification_cycle(expected_station_id)

    def _end_verification_cycle(self, station_id: str):
        """
        Common teardown after every verification path (success, early-exit,
        missed dose).  Disarms the weight sensor, clears medication state,
        and STOPS tag scanning so stale scans do not linger until the next
        dose window.
        """
        self.state_machine.reset_to_idle()
        self.weight_manager.disable_event_detection(station_id)
        self.secured_medications.pop(station_id, None)
        self.current_medication    = None
        self.pending_monitoring_ui = None

        # Stop scanning - scanning resumes the next time the bottle is lifted
        self.tag_runtime_service.stop_scanning()
        self.logger.info(
            f"Tag scanning STOPPED after verification cycle on {station_id}"
        )

        if self.display:
            self.display.show_idle_screen(self.scheduler.get_next_scheduled_time())

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

    def _load_schedule_from_database(self):
        registered = self.database.list_registered_medicines()
        if not registered:
            self.logger.warning("No registered medicines found in database")
            return

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

            self.scheduler.add_medication(
                medicine_name=medicine_name,
                station_id=station_id,
                dosage_pills=dosage,
                times=times
            )
            self.logger.info(
                f"Loaded from DB into scheduler: {medicine_name} "
                f"at {times} on {station_id}"
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

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    def start(self):
        self.logger.info("Starting medication system...")
        self.running = True

        EXPECTED_MEDICINE_COUNT = 3

        registered_before    = self.database.list_registered_medicines()
        onboarding_was_needed = len(registered_before) < EXPECTED_MEDICINE_COUNT

        all_registered = self.registration_manager.run_onboarding_if_needed(
            station_id="station_1",
            expected_medicine_count=EXPECTED_MEDICINE_COUNT,
            scheduler=self.scheduler
        )
        # run_onboarding_if_needed calls stop_scanning() when done,
        # so the reader is idle when we enter the main loop.

        if not all_registered:
            self.logger.error(
                "Onboarding did not complete. System may have partial setup."
            )

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

        if self.display:
            self.display.show_idle_screen(self.scheduler.get_next_scheduled_time())

        self.logger.info("System ready")

        try:
            while self.running:
                self._process_secured_bottle_placements()
                self._process_secured_bottle_movements()
                self._process_pending_manual_reminder()
                self._authorize_current_medication_if_ready()
                self._process_pending_weight_event()
                if self.display:
                    self.display.update()
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
