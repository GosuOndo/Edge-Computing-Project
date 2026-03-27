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

            self.scheduler = MedicationScheduler(
                self.config["schedule"], self.logger
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

    def _process_secured_bottle_placements(self):
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

            scan_msg = latest.get("scan_msg") or {}
            record   = self._resolve_record_from_scan(scan_msg)
            if not record or record.get("station_id") != station_id:
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
                if not match:
                    self.logger.warning(
                        f"Wrong medicine placed on {station_id}: "
                        f"expected {registered.get('medicine_name')} ({registered_medicine_id}), "
                        f"got {record.get('medicine_name')} ({scanned_medicine_id})"
                    )
                    if self.display:
                        self.display.show_warning_screen(
                            "Wrong medicine detected",
                            f"Please place {registered.get('medicine_name', 'the correct medicine')} "
                            f"on {station_id}"
                        )
                    if self.audio:
                        self.audio.speak_async(
                            f"Wrong medicine detected. Please place "
                            f"{registered.get('medicine_name', 'the correct medicine')} "
                            f"on {station_id}"
                        )
                    continue

            status = self.weight_manager.get_station_status(station_id)
            if not status.get("connected"):
                continue

            weight_g = float(status.get("weight_g") or 0.0)
            if not status.get("stable", False):
                continue
            if weight_g < self.min_secured_bottle_weight_g:
                continue

            self._secure_bottle_until_due(record, scan_received_at, weight_g)

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

    def _has_pending_security_violation(self) -> bool:
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
            if secure_state.get("early_alert_sent", False):
                return True
            if secure_state.get("wrong_bottle_on_station", False):
                return True
            if (
                secure_state.get("tamper_alert_sent", False)
                and now_ts < secure_state.get("tamper_alert_until", 0)
            ):
                return True

        return False

    def _get_station_security_issue(self, secure_state: dict):
        if secure_state.get("wrong_bottle_on_station", False):
            return "incorrect"
        if secure_state.get("early_alert_sent", False):
            return "missing"
        if (
            secure_state.get("tamper_alert_sent", False)
            and time.time() < secure_state.get("tamper_alert_until", 0)
        ):
            return "tampered"
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
            if now_ts >= secure_state.get("next_due_timestamp", 0):
                continue
            if secure_state.get("authorized", False):
                continue
            if (
                self.current_medication
                and self.current_medication.get("station_id") == station_id
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
                    mark tamper state so the security-alert screen stays visible
                    for 30 s, update baseline to reflect actual bottle state.
        """
        station_id    = secure_state.get("station_id", "unknown")
        medicine_name = secure_state.get("medicine_name", "Unknown")

        reference_g = secure_state.get("pre_removal_weight_g")
        if not reference_g or reference_g <= 0:
            self.logger.info(
                f"[{station_id}] No pre-removal weight snapshot — "
                "skipping tamper check"
            )
            return

        delta_g = reference_g - returned_weight_g   # positive = bottle is lighter

        # Tamper threshold: station-level override, or half a pill weight
        # (same heuristic used by WeightManager for noise rejection).
        cfg         = self.weight_manager.station_configs.get(station_id, {})
        threshold_g = float(
            cfg.get("tamper_tolerance_g")
            or self.weight_manager._get_min_delta_g(station_id)
        )

        self.logger.info(
            f"[{station_id}] Tamper check: "
            f"ref={reference_g:.2f}g  returned={returned_weight_g:.2f}g  "
            f"delta={delta_g:.2f}g  threshold={threshold_g:.2f}g"
        )

        # Always bring the weight_manager baseline in line with what is
        # physically on the scale now so future dose calculations are accurate.
        if returned_weight_g > 0:
            self.weight_manager.baseline_weights[station_id] = returned_weight_g
            self.weight_manager._save_persisted_baselines()

        if delta_g <= threshold_g:
            self.logger.info(
                f"[{station_id}] Tamper check PASSED — "
                "returned weight within tolerance"
            )
            return

        # ---- Weight discrepancy detected ----
        pill_weight_g    = self.weight_manager._get_pill_weight_g(station_id)
        estimated_pills  = (
            max(1, round(delta_g / pill_weight_g))
            if pill_weight_g > 0 else "unknown"
        )
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

        # Flag tamper state – keeps the security-alert screen visible for 30 s
        # via _get_station_security_issue / _has_pending_security_violation.
        secure_state["tamper_alert_sent"]  = True
        secure_state["tamper_alert_until"] = time.time() + 30
        secure_state["tamper_delta_g"]     = round(delta_g, 2)
        secure_state["tamper_pills_est"]   = estimated_pills
        # Immediately refresh the security screen to show the tamper alert.
        self._refresh_security_violation_screen()

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
        record   = self._resolve_record_from_scan(scan_msg)

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

        correct = (
            (expected_medicine_id and actual_medicine_id == expected_medicine_id)
            or (expected_tag_uid and actual_tag_uid == expected_tag_uid)
        )

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
            if self.display:
                self.display.show_error_screen(
                    f"Wrong bottle on {station_id}. Please replace with "
                    f"{secure_state.get('medicine_name', 'the correct medication')}."
                )
            if self.audio:
                self.audio.speak_async(
                    f"Wrong bottle detected. Please replace with "
                    f"{secure_state.get('medicine_name', 'the correct medication')}"
                )

        self.tag_runtime_service.stop_scanning(station_id)
        return correct

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

        if self.state_machine.get_state() != SystemState.REMINDER_ACTIVE:
            return False

        station_id = self.current_medication["station_id"]
        status     = self.weight_manager.get_station_status(station_id)
        if status.get("event_detection_enabled"):
            return True
        if not status.get("connected"):
            return False

        # ---- Fast path -------------------------------------------------------
        # _recapture_fresh_baseline() already captured a settle-verified
        # baseline for this dose window.  Skip the single-sample capture and
        # arm the sensor immediately.
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

        # ---- Slow path -------------------------------------------------------
        # Fresh recapture was not possible (bottle absent, sensor offline, or
        # timeout).  Fall back to the single-sample stable check so the system
        # is never stuck waiting for authorization.
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
        self.tag_runtime_service.start_scanning(station_id)

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
        baseline for station_id.  Uses the same settle-time logic as the
        pill-removal FSM: the sensor must report stable continuously for
        event_settle_seconds before the capture is accepted.  This rejects
        transient 'stable' blips caused by load-cell thermal creep so the
        dose-window delta is always computed against an accurate full-bottle
        weight, not the (potentially drifted) registration-time value.

        Blocks up to `timeout` seconds.  Returns True on success, False if
        the bottle is absent, the sensor is offline, or the timeout expires.
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
        stable_since = None   # wall-clock time when current stable streak began

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
                # Bottle not on scale.
                stable_since = None
                time.sleep(0.2)
                continue

            if not is_stable:
                # Reading still fluctuating – reset the streak.
                stable_since = None
                time.sleep(0.1)
                continue

            # Bottle present and reading is stable.
            if stable_since is None:
                stable_since = time.time()

            if time.time() - stable_since < settle_time:
                # Not stable long enough yet.
                time.sleep(0.1)
                continue

            # Stable for the full settle period – capture.
            ok = self.weight_manager.capture_current_baseline(station_id)
            if ok:
                self.logger.info(
                    f"[{station_id}] Fresh baseline captured: {weight_g:.2f}g "
                    f"(stable for ≥{settle_time:.1f}s)"
                )
                return True

            # capture_current_baseline can fail if weight_data changed between
            # the status poll and the capture call (MQTT race).  Retry.
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

        # Force a fresh baseline capture for this dose window so load-cell
        # creep since the last capture does not skew pill-count accuracy.
        # Mark as required first so _authorize_current_medication_if_ready
        # uses the slow-path fallback if recapture fails.
        self.weight_manager.baseline_capture_required[station_id] = True
        self._recapture_fresh_baseline(station_id)

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
            self._show_idle_screen()

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

        # Per-event weight check (used for the raw delta / pill_weight metadata).
        weight_result = self.weight_manager.verify_dosage(
            expected_station_id, expected_dosage
        )
        self.logger.info(f"Weight verification (per-event): {weight_result}")

        # Build a cumulative weight result that reflects all pills removed
        # during this dose window (across every lift-and-replace cycle).
        cumulative_pills = self._dose_pills_removed.get(expected_station_id, 0)
        pill_weight_g    = float(
            weight_result.get("pill_weight_g")
            or self.weight_manager._get_pill_weight_g(expected_station_id)
        )
        cum_delta_g      = cumulative_pills * pill_weight_g
        exp_delta_g      = expected_dosage  * pill_weight_g
        cum_delta_err    = abs(cum_delta_g - exp_delta_g)
        tolerance_g      = float(
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
            decision = self.decision_engine.verify_medication_intake(
                expected_medicine=medicine_name,
                expected_dosage=expected_dosage,
                identity_result=identity_result,
                ocr_result=None,
                weight_result=cumulative_weight_result,
                monitoring_result=None
            )
            self._handle_decision(decision)
            self.database.log_medication_event(decision)
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
                if self.display:
                    self.display.show_overdose_screen(
                        medicine_name, cumulative_pills, expected_dosage
                    )
                if self.audio:
                    self.audio.announce_warning(
                        f"Too many pills detected. "
                        f"You took {cumulative_pills} but only {expected_dosage} "
                        f"are required. Please contact your caregiver."
                    )
                self.telegram.send_incorrect_dosage_alert(
                    medicine_name=medicine_name,
                    expected=expected_dosage,
                    actual=cumulative_pills
                )
                decision = self.decision_engine.verify_medication_intake(
                    expected_medicine=medicine_name,
                    expected_dosage=expected_dosage,
                    identity_result=identity_result,
                    ocr_result=ocr_result,
                    weight_result=cumulative_weight_result,
                    monitoring_result=None
                )
                self.database.log_medication_event(decision)
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
        # Monitoring + intake retry loop
        # ------------------------------------------------------------------
        # Run the first monitoring session then keep looping until the
        # cumulative swallow count matches the expected dosage, the patient
        # times out, or an over-count is detected.
        monitoring_result = self._run_monitoring_session(expected_dosage)
        total_swallows    = int(monitoring_result.get("swallow_count", 0))

        while total_swallows != expected_dosage:
            if not self.running:
                return

            # ---- Over-count ----
            if total_swallows > expected_dosage:
                self.logger.warning(
                    f"Intake excess: {total_swallows} swallow(s), "
                    f"only ~{expected_dosage} expected for {medicine_name}"
                )
                if self.display:
                    self.display.show_warning_screen(
                        "Too Many Intakes Detected",
                        f"Detected {total_swallows} swallow(s) but only "
                        f"{expected_dosage} expected for {medicine_name}.\n"
                        "Your caregiver has been notified."
                    )
                if self.audio:
                    self.audio.announce_warning(
                        f"Too many intakes detected. {total_swallows} swallows "
                        f"but only {expected_dosage} expected for {medicine_name}. "
                        "Your caregiver has been notified."
                    )
                self.telegram.send_incorrect_dosage_alert(
                    medicine_name=medicine_name,
                    expected=expected_dosage,
                    actual=total_swallows
                )
                time.sleep(5)
                break

            # ---- Under-count: prompt patient and wait for more pills ----
            remaining = expected_dosage - total_swallows
            pill_word = "pill" if remaining == 1 else "pills"
            self.logger.warning(
                f"Intake mismatch: {total_swallows} swallow(s) detected, "
                f"~{expected_dosage} expected for {medicine_name}"
            )
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

            # Return to REMINDER_ACTIVE so _on_pill_removal queues the
            # next event.  The mismatch screen stays visible while we wait.
            self.state_machine.transition_to(
                SystemState.REMINDER_ACTIVE, self.current_medication
            )

            next_event = self._wait_for_pill_removal_event(timeout_seconds=120.0)

            if next_event is None:
                # Patient did not respond within the timeout window.
                self.logger.warning("Intake retry timed out – patient did not respond")
                self.telegram.send_incorrect_dosage_alert(
                    medicine_name=medicine_name,
                    expected=expected_dosage,
                    actual=total_swallows
                )
                break

            # Patient removed more pills – monitor the additional intake.
            retry_result    = self._run_monitoring_session(expected_dosage)
            new_swallows    = int(retry_result.get("swallow_count", 0))
            total_swallows += new_swallows
            monitoring_result = retry_result
            self.logger.info(
                f"Retry monitoring: +{new_swallows} swallows "
                f"(total {total_swallows}/{expected_dosage})"
            )

        # Propagate the final cumulative count so the decision engine and
        # the database record see the accurate total.
        monitoring_result["swallow_count"] = total_swallows

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

    def _run_monitoring_session(self, expected_dosage: int) -> dict:
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
                }

            while self.patient_monitor.is_monitoring_active():
                if not self.running:
                    self.patient_monitor.cleanup()
                    return {
                        "compliance_status": "unclear",
                        "swallow_count":     0,
                        "cough_count":       0,
                        "hand_motion_count": 0,
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
            }

    def _wait_for_pill_removal_event(self, timeout_seconds: float = 120.0):
        """
        Block until the weight sensor fires a pill-removal event or the
        timeout expires.  The caller must have already transitioned to
        REMINDER_ACTIVE so that _on_pill_removal() queues the event.

        Returns the event dict on success, or None on timeout / shutdown.
        The mismatch screen shown by the caller remains visible throughout
        because no display call is made here.
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
        and STOPS tag scanning so stale scans do not linger until the next
        dose window.
        """
        self.state_machine.reset_to_idle()
        self.weight_manager.disable_event_detection(station_id)
        self.secured_medications.pop(station_id, None)
        self.current_medication    = None
        self.pending_monitoring_ui = None
        self._last_security_violation_message = None

        # Clear per-dose retry counters for this station.
        self._dose_pills_removed.pop(station_id, None)
        self._dose_attempt_count.pop(station_id, None)

        # Stop scanning - scanning resumes the next time the bottle is lifted
        self.tag_runtime_service.stop_scanning()
        self.logger.info(
            f"Tag scanning STOPPED after verification cycle on {station_id}"
        )

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

        registered_before = self.database.list_registered_medicines()
        registered_station_ids = {r.get("station_id") for r in registered_before}
        onboarding_was_needed = any(
            sid not in registered_station_ids for sid in STATION_IDS
        )

        all_registered = True
        for station_id in STATION_IDS:
            ok = self.registration_manager.run_onboarding_if_needed(
                station_id=station_id,
                expected_medicine_count=EXPECTED_MEDICINE_COUNT,
                scheduler=self.scheduler
            )
            if not ok:
                self.logger.error(
                    f"Onboarding did not complete for {station_id}. "
                    "System may have partial setup."
                )
                all_registered = False
        # run_onboarding_if_needed calls stop_scanning() per station when done,
        # so all readers are idle when we enter the main loop.

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
            self._show_idle_screen()

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
