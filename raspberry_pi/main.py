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


class MedicationSystem:
    """Main medication verification system orchestrator"""

    def __init__(self, config_path='config/config.yaml', enable_display=True, enable_audio=True):
        """Initialize the medication system"""

        # Load configuration
        self.config = get_config(config_path)

        # Initialize logger
        self.logger = get_logger(self.config.get_logging_config())
        self.logger.info("=" * 60)
        self.logger.info("Smart Medication Verification System Starting")
        self.logger.info("=" * 60)

        # Mode flags
        self.enable_display = enable_display
        self.enable_audio = enable_audio

        # System state
        self.running = False
        self.state_machine = StateMachine(self.logger)

        # Current medication context
        self.current_medication = None

        self._stop_called = False

        # Thread-safe queued weight event handling
        self.pending_weight_event = None
        self.pending_weight_lock = False

        # Thread-safe queued manual reminder
        self.pending_manual_reminder = None
        self.pending_manual_reminder_lock = False

        # Monitoring UI state rendered only on main thread
        self.pending_monitoring_ui = None

        # Initialize all modules
        self._initialize_modules()

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _initialize_modules(self):
        """Initialize all system modules"""
        self.logger.info("Initializing modules...")

        try:
            # Database
            self.database = Database(self.config['database'], self.logger)
            self.database.connect()

            # MQTT Client
            self.mqtt = MQTTClient(self.config.get_mqtt_config(), self.logger)
            self.mqtt.set_weight_callback(self._on_weight_data)
            self.mqtt.connect()

            # Weight Manager
            self.weight_manager = WeightManager(
                self.config['weight_sensors'],
                self.logger
            )
            self.weight_manager.set_pill_removal_callback(self._on_pill_removal)

            # Medicine Scanner (OCR)
            self.scanner = MedicineScanner(self.config['ocr'], self.logger)

            # Patient Monitor (MediaPipe)
            self.patient_monitor = PatientMonitor(
                self.config['patient_monitoring'],
                self.logger
            )

            # Telegram Bot
            self.telegram = TelegramBot(self.config['telegram'], self.logger)
            self.telegram.start_queue_processor()

            # Display Manager
            if self.enable_display:
                self.display = DisplayManager(
                    self.config['hardware']['display'],
                    self.logger
                )
                self.display.initialize()
                self.logger.info("Display manager initialized")
            else:
                self.display = None
                self.logger.info("Display manager skipped (headless test mode)")

            # Audio Manager
            if self.enable_audio:
                self.audio = AudioManager(
                    self.config['hardware']['audio'],
                    self.logger
                )
                
                ok = self.audio.initialize()
                if ok:
                    self.logger.info("Audio manager initialized")
                else:
                    self.logger.warning("Audio manager failed to initialize; continuing without audio")
            else:
                self.audio = None
                self.logger.info("Audio manager skipped (headless test mode)")
                
            # Decision Engine
            self.decision_engine = DecisionEngine(
                self.config['decision_engine'],
                self.logger
            )

            # Medication Scheduler
            self.scheduler = MedicationScheduler(
                self.config['schedule'],
                self.logger
            )
            self.scheduler.set_reminder_callback(self._on_medication_reminder)
            self.scheduler.set_missed_dose_callback(self._on_missed_dose)

            self.logger.info("All modules initialized successfully")

        except Exception as e:
            self.logger.critical(f"Module initialization failed: {e}")
            raise

    def queue_manual_reminder(self, reminder_data):
        """Queue a manual reminder to be processed safely on the main thread."""
        self.pending_manual_reminder = reminder_data
        self.logger.info(f"Queued manual reminder: {reminder_data}")

    def _process_pending_manual_reminder(self):
        """Process queued manual reminder safely from the main thread."""
        if self.pending_manual_reminder_lock:
            return

        if not self.pending_manual_reminder:
            return

        reminder_data = self.pending_manual_reminder
        self.pending_manual_reminder = None

        self.pending_manual_reminder_lock = True
        try:
            self._on_medication_reminder(reminder_data)
        finally:
            self.pending_manual_reminder_lock = False

    def _on_weight_data(self, data):
        """Handle incoming weight data from M5StickC"""
        self.weight_manager.process_weight_data(data)

    def _on_pill_removal(self, event_data):
        """
        Handle pill removal event from weight sensor.

        IMPORTANT:
        This callback may be triggered from the MQTT thread.
        Do not run display-heavy verification logic directly here.
        Instead, queue the event for the main thread to process safely.
        """
        self.logger.info(
            f"Pill removal detected: {event_data['pills_removed']} pills "
            f"from {event_data['station_id']}"
        )

        if self.state_machine.get_state() != SystemState.REMINDER_ACTIVE:
            return

        station_id = event_data["station_id"]

        if self.current_medication and self.current_medication["station_id"] == station_id:
            self.pending_weight_event = event_data
            self.logger.info("Queued weight event for main-thread verification")

    def _process_pending_weight_event(self):
        """Process queued weight event safely from the main thread."""
        if self.pending_weight_lock:
            return

        if not self.pending_weight_event:
            return

        event_data = self.pending_weight_event
        self.pending_weight_event = None

        self.pending_weight_lock = True
        try:
            self.state_machine.transition_to(
                SystemState.VERIFYING,
                {"event_data": event_data}
            )
            self._verify_medication_intake(event_data)
        finally:
            self.pending_weight_lock = False

    def _render_pending_monitoring_ui(self):
        """Render monitoring UI only from the main thread."""
        if not self.display:
            return

        if not self.pending_monitoring_ui:
            return

        elapsed, duration, message = self.pending_monitoring_ui
        self.display.show_monitoring_screen(elapsed, duration, message)
        
    def _on_medication_reminder(self, reminder_data):
        """Handle medication reminder from scheduler"""
        self.logger.info(f"Medication reminder triggered: {reminder_data}")

        # Store current medication context
        self.current_medication = reminder_data

        station_id = reminder_data['station_id']
        self.weight_manager.enable_event_detection(station_id)

        # Transition to reminder active state
        self.state_machine.transition_to(
            SystemState.REMINDER_ACTIVE,
            reminder_data
        )

        medicine_name = reminder_data['medicine_name']
        dosage = reminder_data['dosage_pills']
        time_str = reminder_data['scheduled_time']

        if self.display:
            self.display.show_reminder_screen(medicine_name, dosage, time_str)

        if self.audio:
            self.audio.announce_reminder(medicine_name, dosage)

        self.telegram.send_medication_reminder(medicine_name, dosage, time_str)

    def _on_missed_dose(self, missed_data):
        """Handle missed dose notification"""
        self.logger.warning(f"Missed dose: {missed_data}")

        self.telegram.send_missed_dose_alert(
            missed_data['medicine_name'],
            missed_data['scheduled_time'],
            missed_data['timeout_minutes']
        )

        if self.display:
            self.display.show_warning_screen(
                "Missed Dose",
                f"{missed_data['medicine_name']} not taken"
            )

        if self.audio:
            self.audio.announce_warning("Medication dose was missed")

        missed_event = {
            'timestamp': time.time(),
            'expected_medicine': missed_data['medicine_name'],
            'expected_dosage': 0,
            'result': DecisionResult.NO_INTAKE,
            'verified': False,
            'alerts': [{'type': 'missed_dose', 'severity': 'critical', 'message': 'Dose not taken'}],
            'details': {},
            'scores': {}
        }
        self.database.log_medication_event(missed_event)

        self.state_machine.reset_to_idle()

        if self.current_medication:
            station_id = self.current_medication['station_id']
            self.weight_manager.disable_event_detection(station_id)

        self.current_medication = None
        self.pending_monitoring_ui = None

        if self.display:
            self.display.show_idle_screen(self.scheduler.get_next_scheduled_time())

    def _verify_medication_intake(self, weight_event):
        """Verify medication intake using all sensors"""
        self.logger.info("Starting medication verification...")

        if not self.running:
            self.logger.info("System stopping; aborting verification early.")
            return

        medicine_name = self.current_medication['medicine_name']
        expected_dosage = self.current_medication['dosage_pills']

        # Step 1: OCR Verification
        if self.display:
            self.display.show_monitoring_screen(0, 5, "Scanning label...")

        ocr_result = None
        try:
            self.scanner.initialize_camera()
            ocr_result = self.scanner.scan_label(num_attempts=2)
            self.scanner.release_camera()
            self.logger.info(f"OCR result: {ocr_result}")
        except Exception as e:
            self.logger.warning(f"OCR verification failed: {e}")

        if not self.running:
            self.logger.info("System stopping; aborting verification early.")
            return

        # Step 2: Weight Verification
        weight_result = self.weight_manager.verify_dosage(
            self.current_medication['station_id'],
            expected_dosage
        )
        self.logger.info(f"Weight verification: {weight_result}")
        
        if not self.running:
            self.logger.info("System stopping; aborting verification early.")
            return

        # Step 3: Patient Monitoring
        self.logger.info("Starting patient monitoring (30 seconds)...")
        self.state_machine.transition_to(SystemState.MONITORING_PATIENT)

        monitoring_result = None
        self.pending_monitoring_ui = (0, 30, "Monitoring intake...")

        try:
            def progress_callback(detections, elapsed, duration):
                # Never touch pygame/display here.
                self.pending_monitoring_ui = (elapsed, duration, "Monitoring intake...")

            started = self.patient_monitor.start_monitoring(duration=30, callback=progress_callback)
            if not started:
                self.logger.warning("Patient monitoring could not be started")
                monitoring_result = {
                    'compliance_status': 'no_intake',
                    'swallow_count': 0,
                    'cough_count': 0,
                    'hand_motion_count': 0
                }
            else:
                while self.patient_monitor.is_monitoring_active():
                    if not self.running:
                        self.logger.info("System stopping while monitoring.")
                        self.patient_monitor.cleanup()
                        return

                    if self.display:
                        self._render_pending_monitoring_ui()
                        self.display.update()

                    time.sleep(0.1)

                monitoring_result = self.patient_monitor.get_results()
                self.logger.info(f"Monitoring complete: {monitoring_result['compliance_status']}")

        except Exception as e:
            self.logger.error(f"Patient monitoring failed: {e}")

        if not self.running:
            self.logger.info("System stopping; aborting verification before decision.")
            return

        # Step 4: Decision
        self.logger.info("Making verification decision...")

        decision = self.decision_engine.verify_medication_intake(
            expected_medicine=medicine_name,
            expected_dosage=expected_dosage,
            ocr_result=ocr_result,
            weight_result=weight_result,
            monitoring_result=monitoring_result
        )

        # Step 5: Act on decision
        self._handle_decision(decision)

        if decision['verified']:
            self.scheduler.mark_dose_taken(medicine_name)

        self.database.log_medication_event(decision)

        time.sleep(3)
        self.state_machine.reset_to_idle()

        station_id = self.current_medication['station_id']
        self.weight_manager.disable_event_detection(station_id)

        self.current_medication = None
        self.pending_monitoring_ui = None

        if self.display:
            self.display.show_idle_screen(self.scheduler.get_next_scheduled_time())

    def _handle_decision(self, decision):
        """Handle decision result and send appropriate feedback"""
        result = decision['result']
        verified = decision['verified']
        medicine_name = decision['expected_medicine']

        self.logger.info(f"Decision: {result.value} (verified: {verified})")

        messages = self.decision_engine.get_alert_messages(decision)

        if verified and result == DecisionResult.SUCCESS:
            if self.display:
                self.display.show_success_screen(
                    medicine_name,
                    "Medication taken successfully!"
                )
            if self.audio:
                self.audio.announce_success(medicine_name)

            self.telegram.send_dose_taken_confirmation(
                medicine_name,
                decision['expected_dosage']
            )
            
        elif result == DecisionResult.INCORRECT_DOSAGE:
            expected = decision['expected_dosage']
            actual = decision['details'].get('weight_actual', 0)

            if self.display:
                self.display.show_warning_screen(
                    "Incorrect Dosage",
                    f"Expected {expected} pills, detected {actual} pills"
                )
            if self.audio:
                self.audio.announce_warning(messages['patient_message'])

            self.telegram.send_incorrect_dosage_alert(medicine_name, expected, actual)

        elif result == DecisionResult.BEHAVIORAL_ISSUE:
            if self.display:
                self.display.show_warning_screen(
                    "Monitoring Alert",
                    messages['patient_message']
                )
            if self.audio:
                self.audio.announce_warning(messages['patient_message'])

            self.telegram.send_behavioral_alert(
                medicine_name,
                'concerning',
                decision['details']
            )

        elif result == DecisionResult.NO_INTAKE:
            if self.display:
                self.display.show_warning_screen(
                    "No Intake Detected",
                    "Please take your medication"
                )
            if self.audio:
                self.audio.announce_warning("No medication intake detected")

        else:
            if self.display:
                self.display.show_warning_screen(
                    "Verification Warning",
                    messages['patient_message']
                )

            if self.decision_engine.should_alert_caregiver(decision):
                self.telegram.send_message(
                    self.telegram.caregiver_chat_id,
                    messages['caregiver_message']
                )

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.logger.info("Shutdown signal received")
        self.stop()

    def start(self):
        """Start the medication system"""
        self.logger.info("Starting medication system...")

        self.running = True

        self.scheduler.start()

        if self.display:
            self.display.show_idle_screen(self.scheduler.get_next_scheduled_time())

        self.logger.info("System ready!")

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
                self.display.show_error_screen(f"System error: {str(e)}")
        finally:
            self.stop()

    def stop(self):
        """Stop the medication system gracefully"""
        if hasattr(self, "_stop_called") and self._stop_called:
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

            if hasattr(self, "database") and self.database:
                self.database.cleanup()

            self.logger.info("System stopped gracefully")
            self.logger.info("=" * 60)

        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}")


def main():
    """Main entry point"""

    config_path = Path('config/config.yaml')
    if not config_path.exists():
        print("ERROR: Configuration file not found!")
        print("Please copy config.example.yaml to config.yaml and configure it.")
        print(f"Expected location: {config_path.absolute()}")
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


if __name__ == '__main__':
    main()
