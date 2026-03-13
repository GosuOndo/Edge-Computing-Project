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
    
    def __init__(self, config_path='config/config.yaml'):
        """Initialize the medication system"""
        
        # Load configuration
        self.config = get_config(config_path)
        
        # Initialize logger
        self.logger = get_logger(self.config.get_logging_config())
        self.logger.info("="*60)
        self.logger.info("Smart Medication Verification System Starting")
        self.logger.info("="*60)
        
        # System state
        self.running = False
        self.state_machine = StateMachine(self.logger)
        
        # Current medication context
        self.current_medication = None
        
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
            self.display = DisplayManager(
                self.config['hardware']['display'], 
                self.logger
            )
            self.display.initialize()
            
            # Audio Manager
            self.audio = AudioManager(
                self.config['hardware']['audio'], 
                self.logger
            )
            self.audio.initialize()
            
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
    
    def _on_weight_data(self, data):
        """Handle incoming weight data from M5StickC"""
        self.weight_manager.process_weight_data(data)
    
    def _on_pill_removal(self, event_data):
        """Handle pill removal event from weight sensor"""
        self.logger.info(
            f"Pill removal detected: {event_data['pills_removed']} pills "
            f"from {event_data['station_id']}"
        )
        
        # Only process if we're expecting medication
        if self.state_machine.get_state() == SystemState.REMINDER_ACTIVE:
            station_id = event_data['station_id']
            
            # Check if this is the correct station
            if (self.current_medication and 
                self.current_medication['station_id'] == station_id):
                
                # Transition to verification
                self.state_machine.transition_to(
                    SystemState.VERIFYING,
                    {'event_data': event_data}
                )
                
                # Start verification process
                self._verify_medication_intake(event_data)
    
    def _on_medication_reminder(self, reminder_data):
        """Handle medication reminder from scheduler"""
        self.logger.info(f"Medication reminder triggered: {reminder_data}")
        
        # Store current medication context
        self.current_medication = reminder_data
        
        # Transition to reminder active state
        self.state_machine.transition_to(
            SystemState.REMINDER_ACTIVE,
            reminder_data
        )
        
        # Multi-channel reminder
        medicine_name = reminder_data['medicine_name']
        dosage = reminder_data['dosage_pills']
        time_str = reminder_data['scheduled_time']
        
        # Display reminder
        self.display.show_reminder_screen(medicine_name, dosage, time_str)
        
        # Audio reminder
        self.audio.announce_reminder(medicine_name, dosage)
        
        # Telegram reminder
        self.telegram.send_medication_reminder(medicine_name, dosage, time_str)
        
    def _on_missed_dose(self, missed_data):
        """Handle missed dose notification"""
        self.logger.warning(f"Missed dose: {missed_data}")
        
        # Send alerts
        self.telegram.send_missed_dose_alert(
            missed_data['medicine_name'],
            missed_data['scheduled_time'],
            missed_data['timeout_minutes']
        )
        
        self.display.show_warning_screen(
            "Missed Dose",
            f"{missed_data['medicine_name']} not taken"
        )
        
        self.audio.announce_warning("Medication dose was missed")
        
        # Log to database
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
        
        # Reset to idle
        self.state_machine.reset_to_idle()
        self.current_medication = None
        self.display.show_idle_screen(self.scheduler.get_next_scheduled_time())
    
    def _verify_medication_intake(self, weight_event):
        """Verify medication intake using all sensors"""
        self.logger.info("Starting medication verification...")
        
        medicine_name = self.current_medication['medicine_name']
        expected_dosage = self.current_medication['dosage_pills']
        
        # Step 1: OCR Verification (optional)
        self.display.show_monitoring_screen(0, 5, "Scanning label...")
        ocr_result = None
        
        try:
            self.scanner.initialize_camera()
            ocr_result = self.scanner.scan_label(num_attempts=2)
            self.scanner.release_camera()
            self.logger.info(f"OCR result: {ocr_result}")
        except Exception as e:
            self.logger.warning(f"OCR verification failed: {e}")
        
        # Step 2: Weight Verification
        weight_result = self.weight_manager.verify_dosage(
            self.current_medication['station_id'],
            expected_dosage
        )
        self.logger.info(f"Weight verification: {weight_result}")
        
        # Step 3: Patient Monitoring
        self.logger.info("Starting patient monitoring (30 seconds)...")
        self.state_machine.transition_to(SystemState.MONITORING_PATIENT)
        
        monitoring_result = None
        
        try:
            # Start monitoring with progress callback
            def progress_callback(detections, elapsed, duration):
                self.display.show_monitoring_screen(elapsed, duration, "Monitoring intake...")
            
            self.patient_monitor.start_monitoring(duration=30, callback=progress_callback)
            
            # Wait for completion
            while self.patient_monitor.is_monitoring_active():
                time.sleep(0.5)
                self.display.update()
            
            monitoring_result = self.patient_monitor.get_results()
            self.logger.info(f"Monitoring complete: {monitoring_result['compliance_status']}")
            
        except Exception as e:
            self.logger.error(f"Patient monitoring failed: {e}")
        
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
        
        # Mark dose as taken in scheduler
        if decision['verified']:
            self.scheduler.mark_dose_taken(medicine_name)
        
        # Log to database
        self.database.log_medication_event(decision)
        
        # Return to idle
        time.sleep(3)
        self.state_machine.reset_to_idle()
        self.current_medication = None
        self.display.show_idle_screen(self.scheduler.get_next_scheduled_time())
    
    def _handle_decision(self, decision):
        """Handle decision result and send appropriate feedback"""
        result = decision['result']
        verified = decision['verified']
        medicine_name = decision['expected_medicine']
        
        self.logger.info(f"Decision: {result.value} (verified: {verified})")
        
        # Get alert messages
        messages = self.decision_engine.get_alert_messages(decision)
        
        if verified and result == DecisionResult.SUCCESS:
            # Success - all checks passed
            self.display.show_success_screen(
                medicine_name,
                "Medication taken successfully!"
            )
            self.audio.announce_success(medicine_name)
            self.telegram.send_dose_taken_confirmation(
                medicine_name,
                decision['expected_dosage']
            )
            
        elif result == DecisionResult.INCORRECT_DOSAGE:
            # Wrong dosage
            expected = decision['expected_dosage']
            actual = decision['details'].get('weight_actual', 0)
            
            self.display.show_warning_screen(
                "Incorrect Dosage",
                f"Expected {expected} pills, detected {actual} pills"
            )
            self.audio.announce_warning(messages['patient_message'])
            self.telegram.send_incorrect_dosage_alert(medicine_name, expected, actual)
            
        elif result == DecisionResult.BEHAVIORAL_ISSUE:
            # Behavioral concerns
            self.display.show_warning_screen(
                "Monitoring Alert",
                messages['patient_message']
            )
            self.audio.announce_warning(messages['patient_message'])
            
            # Alert caregiver
            self.telegram.send_behavioral_alert(
                medicine_name,
                'concerning',
                decision['details']
            )
            
        elif result == DecisionResult.NO_INTAKE:
            # No intake detected
            self.display.show_warning_screen(
                "No Intake Detected",
                "Please take your medication"
            )
            self.audio.announce_warning("No medication intake detected")
            
        else:
            # Partial success or other issues
            self.display.show_warning_screen(
                "Verification Warning",
                messages['patient_message']
            )
            
            # Alert caregiver if needed
            if self.decision_engine.should_alert_caregiver(decision):
                # Send to caregiver via Telegram
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
        
        # Start scheduler
        self.scheduler.start()
        
        # Show idle screen
        self.display.show_idle_screen(self.scheduler.get_next_scheduled_time())
        
        self.logger.info("System ready!")
        
        # Main loop
        try:
            while self.running:
                # Update display
                self.display.update()
                
                # Small sleep to prevent CPU overuse
                time.sleep(0.1)
                
        except KeyboardInterrupt:
            self.logger.info("Keyboard interrupt received")
        except Exception as e:
            self.logger.critical(f"Fatal error in main loop: {e}")
            self.display.show_error_screen(f"System error: {str(e)}")
        finally:
            self.stop()
    
    def stop(self):
        """Stop the medication system gracefully"""
        if not self.running:
            return
        
        self.logger.info("Stopping medication system...")
        self.running = False
        
        # Stop scheduler
        self.scheduler.stop()
        
        # Stop patient monitor if running
        self.patient_monitor.cleanup()
        
        # Stop Telegram queue processor
        self.telegram.stop_queue_processor()
        
        # Disconnect MQTT
        self.mqtt.disconnect()
        
        # Cleanup display
        self.display.cleanup()
        
        # Cleanup audio
        self.audio.cleanup()
        
        # Close database
        self.database.cleanup()
        
        self.logger.info("System stopped gracefully")
        self.logger.info("="*60)


def main():
    """Main entry point"""
    
    # Check if config file exists
    config_path = Path('config/config.yaml')
    if not config_path.exists():
        print("ERROR: Configuration file not found!")
        print(f"Please copy config.example.yaml to config.yaml and configure it.")
        print(f"Expected location: {config_path.absolute()}")
        sys.exit(1)
    
    # Create and start system
    try:
        system = MedicationSystem(config_path=str(config_path))
        system.start()
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
