#!/usr/bin/env python3
"""
Smart Medication System - Main Application

Edge-based medication verification system with real-time monitoring.
Orchestrates all modules and handles the complete medication intake workflow.
"""

import sys
import time
import signal
from pathlib import Path

import os
# os.environ["SDL_AUDIODRIVER"] = "pulseaudio"
os.environ["SDL_VIDEO_FBDEV"] = "/dev/fb0"

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Import utilities
from utils.logger import get_logger
from utils.config_loader import get_config

# Import services
from services.mqtt_client import MQTTClient
from services.scheduler import MedicationScheduler
from services.state_machine import StateMachine, SystemState

# Import modules
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
        self.enable_audio = enable_audio
        self.running = False
        self._stop_called = False

        self.state_machine = StateMachine(self.logger)
        self.current_medication = None

        self.pending_weight_event = None
        self.pending_weight_lock = False
        self.pending_manual_reminder = None
        self.pending_manual_reminder_lock = False
        self.pending_monitoring_ui = None

        self._initialize_modules()

        signal.signal(signal.SIGINT, self._signal_handler)
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
            tag_topic = identity_cfg.get("tag", {}).get(
                "mqtt_topic", "medication/tag/read/+"
            )
            self.tag_runtime_service = TagRuntimeService(
                mqtt_config=self.config["mqtt"],
                database=self.database,
                logger=self.logger,
                topic=tag_topic
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
        """
        Look up medicine_id for a station.
        If medicine_name is provided, matches by both station_id AND name
        so that multiple medicines on one station resolve correctly.
        Falls back to the first registered medicine for the station.
        """
        if medicine_name:
            all_registered = self.database.list_registered_medicines()
            for r in all_registered:
                if (r.get("station_id") == station_id and
                        r.get("medicine_name", "").upper() == medicine_name.upper()):
                    return r.get("medicine_id")

        # Fallback: first medicine registered for this station
        registered = self.database.get_registered_medicine_by_station(station_id)
        if registered:
            return registered.get("medicine_id")
        return None

    # ------------------------------------------------------------------
    # Manual reminder injection (test scripts)
    # ------------------------------------------------------------------

    def queue_manual_reminder(self, reminder_data: dict):
        """Used by test scripts to inject a reminder without the scheduler."""
        self.pending_manual_reminder = reminder_data
        self.logger.info(f"Manual reminder queued: {reminder_data}")

    def _process_pending_manual_reminder(self):
        """Drain a queued manual reminder on the main thread."""
        if self.pending_manual_reminder_lock or not self.pending_manual_reminder:
            return
        reminder_data = self.pending_manual_reminder
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
            
    # ------------------------------------------------------------------
    # Main-thread event processing
    # ------------------------------------------------------------------

    def _process_pending_weight_event(self):
        """Drain a pending weight event on the main thread."""
        if self.pending_weight_lock or not self.pending_weight_event:
            return
        event_data = self.pending_weight_event
        self.pending_weight_event = None
        self.pending_weight_lock = True
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

        # Use medicine_id from reminder_data if already provided (e.g. test injection
        # or manual trigger). Otherwise resolve by station + medicine name so that
        # multiple medicines on the same station resolve correctly.
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

        self.weight_manager.enable_event_detection(station_id)
        self.state_machine.transition_to(SystemState.REMINDER_ACTIVE, reminder_data)

        medicine_name = reminder_data["medicine_name"]
        dosage = reminder_data["dosage_pills"]
        time_str = reminder_data["scheduled_time"]

        if self.display:
            self.display.show_reminder_screen(medicine_name, dosage, time_str)
        if self.audio:
            self.audio.announce_reminder(medicine_name, dosage)
        self.telegram.send_medication_reminder(medicine_name, dosage, time_str)

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
            "timestamp": time.time(),
            "expected_medicine": missed_data["medicine_name"],
            "expected_dosage": 0,
            "result": DecisionResult.NO_INTAKE,
            "verified": False,
            "alerts": [
                {
                    "type": "missed_dose",
                    "severity": "critical",
                    "message": "Dose not taken"
                }
            ],
            "details": {},
            "scores": {}
        }
        self.database.log_medication_event(missed_event)
        self.state_machine.reset_to_idle()

        if self.current_medication:
            self.weight_manager.disable_event_detection(
                self.current_medication["station_id"]
            )
        self.current_medication = None
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

        medicine_name = self.current_medication["medicine_name"]
        expected_dosage = self.current_medication["dosage_pills"]
        expected_medicine_id = self.current_medication.get("medicine_id")
        expected_station_id = self.current_medication["station_id"]

        if self.display:
            self.display.show_pipeline_screen(
                "Identity Check",
                f"Checking bottle identity for {medicine_name}"
            )

        weight_event_ts = weight_event.get("timestamp", time.time())
        identity_cfg = self.config.get("identity", {})
        tag_cfg = identity_cfg.get("tag", {})
        integrated_mode = tag_cfg.get("integrated_mode", True)
        coincident_window = tag_cfg.get("coincident_window_seconds", 15.0)

        identity_result = None
        ocr_result = None

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
                    "success": True,
                    "medicine_name": identity_result.get("medicine_name", medicine_name),
                    "confidence": identity_result.get("confidence", 1.0),
                    "verified": True,
                    "method": identity_result.get("method")
                }
            else:
                ocr_result = None

        except Exception as e:
            self.logger.warning(f"Identity verification error: {e}")
            identity_result = {
                "success": False,
                "method": "none",
                "verified": False,
                "reason": str(e)
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
            self.logger.warning("Stopping pipeline early due to identity mismatch/failure")
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
            self.state_machine.reset_to_idle()
            self.weight_manager.disable_event_detection(expected_station_id)
            self.current_medication = None
            self.pending_monitoring_ui = None
            if self.display:
                self.display.show_idle_screen(self.scheduler.get_next_scheduled_time())
            return

        # HARD STOP on wrong dosage
        if not weight_result.get("verified", False):
            self.logger.warning("Stopping pipeline early due to incorrect dosage")
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
            self.state_machine.reset_to_idle()
            self.weight_manager.disable_event_detection(expected_station_id)
            self.current_medication = None
            self.pending_monitoring_ui = None
            if self.display:
                self.display.show_idle_screen(self.scheduler.get_next_scheduled_time())
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
                    "swallow_count": 0,
                    "cough_count": 0,
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
                "swallow_count": 0,
                "cough_count": 0,
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
        self.state_machine.reset_to_idle()
        self.weight_manager.disable_event_detection(expected_station_id)
        self.current_medication = None
        self.pending_monitoring_ui = None

        if self.display:
            self.display.show_idle_screen(self.scheduler.get_next_scheduled_time())

    # ------------------------------------------------------------------
    # Decision handling
    # ------------------------------------------------------------------

    def _handle_decision(self, decision: dict):
        result = decision["result"]
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
        """
        Load all registered medicines from the database into the live scheduler.
        Called on every boot so the schedule is always populated regardless of
        whether onboarding ran this session.
        """
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
        """Build a human-readable sorted schedule list from registered medicines."""
        entries = []
        for m in medicines:
            name   = m.get("medicine_name", "Unknown")
            dosage = m.get("dosage_amount", "?")
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
        """
        Start the system:
        1. Run onboarding for any unregistered medicines
        2. Load all registered medicines into the scheduler from DB
        3. Start the scheduler
        4. Send onboarding summary only when onboarding actually ran
        5. Enter the main event loop
        """
        self.logger.info("Starting medication system...")
        self.running = True

        EXPECTED_MEDICINE_COUNT = 3

        # Capture state BEFORE onboarding so we can tell if it ran
        registered_before = self.database.list_registered_medicines()
        onboarding_was_needed = len(registered_before) < EXPECTED_MEDICINE_COUNT

        all_registered = self.registration_manager.run_onboarding_if_needed(
            station_id="station_1",
            expected_medicine_count=EXPECTED_MEDICINE_COUNT,
            scheduler=self.scheduler
        )

        if not all_registered:
            self.logger.error(
                "Onboarding did not complete. System may have partial setup."
            )

        # Load schedule from DB every boot (handles skip-onboarding case)
        self._load_schedule_from_database()

        # Only send the onboarding complete Telegram message when onboarding
        # actually ran this session, not on every subsequent boot
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
                self._process_pending_manual_reminder()
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
        """Graceful shutdown: stop all background threads and release resources."""
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
