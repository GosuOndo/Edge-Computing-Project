"""
Smart Medication System - Medication Scheduler

Manages medication schedules and triggers reminders at appropriate times.
"""

import schedule
import time
from datetime import datetime, timedelta
from threading import Thread, Event
from typing import Callable, List, Dict, Any


class MedicationScheduler:
    """Handles medication scheduling and reminders"""
    
    def __init__(self, config: dict, logger):
        """
        Initialize medication scheduler
        
        Args:
            config: Schedule configuration dictionary
            logger: Logger instance
        """
        self.config = config
        self.logger = logger
        
        self.medications = config['medications']
        self.reminder_advance_minutes = config['reminder'].get('advance_minutes', 5)
        self.timeout_minutes = config['reminder'].get('timeout_minutes', 30)
        
        # Callbacks
        self.reminder_callback = None
        self.missed_dose_callback = None
        
        # Tracking
        self.pending_reminders = {}  # {medicine_name: {'time': ..., 'reminded': bool}}
        self.taken_today = {}  # {medicine_name: [timestamps]}
        
        # Scheduler thread
        self.running = False
        self.scheduler_thread = None
        self.stop_event = Event()
        
        self.logger.info(f"Medication scheduler initialized with {len(self.medications)} medications")
    
    def _schedule_medication(self, medication: Dict[str, Any]):
        """
        Schedule reminders for a medication
        
        Args:
            medication: Medication configuration dict
        """
        name = medication['name']
        times = medication['times']
        dosage = medication['dosage_pills']
        station_id = medication['station_id']
        
        for scheduled_time in times:
            # Schedule the actual reminder
            schedule.every().day.at(scheduled_time).do(
                self._trigger_reminder,
                medicine_name=name,
                dosage=dosage,
                station_id=station_id,
                scheduled_time=scheduled_time
            )
            
            self.logger.info(f"Scheduled: {name} ({dosage} pills) at {scheduled_time} on {station_id}")
    
    def _trigger_reminder(self, medicine_name: str, dosage: int, station_id: str, scheduled_time: str):
        """
        Trigger medication reminder
        
        Args:
            medicine_name: Name of medication
            dosage: Number of pills
            station_id: Weight sensor station ID
            scheduled_time: Scheduled time string
        """
        self.logger.info(f"Triggering reminder: {medicine_name} - {dosage} pills")
        
        # Create reminder data
        reminder_data = {
            'medicine_name': medicine_name,
            'dosage_pills': dosage,
            'station_id': station_id,
            'scheduled_time': scheduled_time,
            'actual_time': datetime.now().strftime('%H:%M:%S'),
            'timestamp': time.time()
        }
        
        # Track pending reminder
        self.pending_reminders[medicine_name] = {
            'time': time.time(),
            'reminded': True,
            'data': reminder_data
        }
        
        # Call reminder callback
        if self.reminder_callback:
            try:
                self.reminder_callback(reminder_data)
            except Exception as e:
                self.logger.error(f"Error in reminder callback: {e}")
        
        # Schedule missed dose check
        timeout_seconds = self.timeout_minutes * 60
        Thread(
            target=self._check_missed_dose,
            args=(medicine_name, timeout_seconds),
            daemon=True
        ).start()
    
    def _check_missed_dose(self, medicine_name: str, timeout_seconds: int):
        """
        Check if dose was missed after timeout
        
        Args:
            medicine_name: Name of medication
            timeout_seconds: Timeout in seconds
        """
        time.sleep(timeout_seconds)
        
        # Check if medication was taken
        if medicine_name in self.pending_reminders:
            pending = self.pending_reminders[medicine_name]
            
            if pending['reminded']:  # Still pending (not marked as taken)
                self.logger.warning(f"Missed dose detected: {medicine_name}")
                
                if self.missed_dose_callback:
                    try:
                        self.missed_dose_callback({
                            'medicine_name': medicine_name,
                            'scheduled_time': pending['data']['scheduled_time'],
                            'timeout_minutes': self.timeout_minutes
                        })
                    except Exception as e:
                        self.logger.error(f"Error in missed dose callback: {e}")
    
    def mark_dose_taken(self, medicine_name: str):
        """
        Mark medication as taken
        
        Args:
            medicine_name: Name of medication
        """
        if medicine_name in self.pending_reminders:
            self.pending_reminders[medicine_name]['reminded'] = False
            
            # Track taken doses
            if medicine_name not in self.taken_today:
                self.taken_today[medicine_name] = []
            self.taken_today[medicine_name].append(time.time())
            
            self.logger.info(f"Dose marked as taken: {medicine_name}")
    
    def is_pending(self, medicine_name: str) -> bool:
        """
        Check if medication has pending reminder
        
        Args:
            medicine_name: Name of medication
            
        Returns:
            True if pending
        """
        if medicine_name in self.pending_reminders:
            return self.pending_reminders[medicine_name]['reminded']
        return False
    
    def get_pending_reminder(self, medicine_name: str) -> Dict[str, Any]:
        """
        Get pending reminder data
        
        Args:
            medicine_name: Name of medication
            
        Returns:
            Reminder data or None
        """
        if medicine_name in self.pending_reminders and self.pending_reminders[medicine_name]['reminded']:
            return self.pending_reminders[medicine_name]['data']
        return None
        
    def set_reminder_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """
        Set callback for medication reminders
        
        Args:
            callback: Function to call when reminder triggered
        """
        self.reminder_callback = callback
        self.logger.info("Reminder callback registered")
    
    def set_missed_dose_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """
        Set callback for missed doses
        
        Args:
            callback: Function to call when dose is missed
        """
        self.missed_dose_callback = callback
        self.logger.info("Missed dose callback registered")
    
    def _scheduler_loop(self):
        """Main scheduler loop (runs in thread)"""
        self.logger.info("Scheduler loop started")
        
        while not self.stop_event.is_set():
            schedule.run_pending()
            time.sleep(1)  # Check every second
        
        self.logger.info("Scheduler loop stopped")
    
    def start(self):
        """Start the medication scheduler"""
        if self.running:
            self.logger.warning("Scheduler already running")
            return
        
        # Schedule all medications
        for medication in self.medications:
            self._schedule_medication(medication)
        
        # Start scheduler thread
        self.running = True
        self.stop_event.clear()
        self.scheduler_thread = Thread(target=self._scheduler_loop, daemon=True)
        self.scheduler_thread.start()
        
        self.logger.info("Medication scheduler started")
    
    def stop(self):
        """Stop the medication scheduler"""
        if not self.running:
            return
        
        self.running = False
        self.stop_event.set()
        
        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=5)
        
        schedule.clear()
        self.logger.info("Medication scheduler stopped")
    
    def get_next_scheduled_time(self) -> Dict[str, str]:
        """
        Get next scheduled medication time
        
        Returns:
            Dictionary with medication name and time
        """
        jobs = schedule.get_jobs()
        
        if not jobs:
            return None
        
        # Find next job
        next_job = min(jobs, key=lambda j: j.next_run)
        
        return {
            'medicine_name': next_job.job_func.args[0] if next_job.job_func.args else 'Unknown',
            'time': next_job.next_run.strftime('%H:%M:%S'),
            'time_until': str(next_job.next_run - datetime.now())
        }
    
    def get_todays_schedule(self) -> List[Dict[str, Any]]:
        """
        Get today's complete medication schedule
        
        Returns:
            List of scheduled medications with times
        """
        schedule_list = []
        
        for medication in self.medications:
            for scheduled_time in medication['times']:
                schedule_list.append({
                    'medicine_name': medication['name'],
                    'dosage_pills': medication['dosage_pills'],
                    'station_id': medication['station_id'],
                    'time': scheduled_time,
                    'taken': medication['name'] in self.taken_today
                })
        
        # Sort by time
        schedule_list.sort(key=lambda x: x['time'])
        
        return schedule_list
