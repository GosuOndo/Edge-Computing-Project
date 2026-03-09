"""
Smart Medication System - Telegram Bot Module

Handles all Telegram notifications including patient reminders and caregiver alerts.
Implements offline queueing for reliability when network is unavailable.
"""

import asyncio
from telegram import Bot
from telegram.error import TelegramError, NetworkError, RetryAfter
import time
from typing import Dict, Any, List, Optional
from collections import deque
from threading import Thread, Lock
import json
from pathlib import Path


class TelegramBot:
    """
    Telegram bot for medication reminders and alerts
    
    Sends:
    - Patient reminders (scheduled medication times)
    - Caregiver alerts (missed doses, incorrect dosage, behavioral issues)
    - Compliance reports (daily summaries)
    """
    
    def __init__(self, config: dict, logger):
        """
        Initialize Telegram bot
        
        Args:
            config: Telegram configuration dictionary
            logger: Logger instance
        """
        self.config = config
        self.logger = logger
        
        # Configuration
        self.enabled = config.get('enabled', True)
        self.bot_token = config.get('bot_token')
        self.patient_chat_id = config.get('patient_chat_id')
        self.caregiver_chat_id = config.get('caregiver_chat_id')
        self.retry_attempts = config.get('retry_attempts', 3)
        self.retry_delay = config.get('retry_delay_seconds', 5)
        
        # Bot instance
        self.bot = None
        self.loop = None
        
        # Message queue for offline mode
        self.message_queue = deque(maxlen=100)
        self.queue_lock = Lock()
        self.queue_file = Path('data/telegram_queue.json')
        
        # Connection state
        self.is_online = False
        self.last_connection_check = 0
        
        # Background queue processor
        self.queue_processor_running = False
        self.queue_processor_thread = None
        
        # Validate configuration
        if self.enabled:
            self._validate_config()
            self._initialize_bot()
            self._load_queued_messages()
        
        self.logger.info(f"Telegram bot initialized (enabled: {self.enabled})")
    
    def _validate_config(self):
        """Validate Telegram configuration"""
        if not self.bot_token or 'YOUR_BOT_TOKEN' in self.bot_token:
            raise ValueError(
                "Invalid Telegram bot token. Please configure TELEGRAM_BOT_TOKEN "
                "in config.yaml or .env file"
            )
        
        if not self.patient_chat_id:
            self.logger.warning("Patient chat ID not configured")
        
        if not self.caregiver_chat_id:
            self.logger.warning("Caregiver chat ID not configured")
    
    def _initialize_bot(self):
        """Initialize Telegram bot instance"""
        try:
            self.bot = Bot(token=self.bot_token)
            self.logger.info("Telegram bot instance created")
        except Exception as e:
            self.logger.error(f"Failed to initialize Telegram bot: {e}")
            raise
            
    def _load_queued_messages(self):
        """Load queued messages from disk"""
        if self.queue_file.exists():
            try:
                with open(self.queue_file, 'r') as f:
                    queued = json.load(f)
                    for msg in queued:
                        self.message_queue.append(msg)
                self.logger.info(f"Loaded {len(queued)} queued messages from disk")
            except Exception as e:
                self.logger.error(f"Failed to load queued messages: {e}")
    
    def _save_queued_messages(self):
        """Save queued messages to disk"""
        try:
            self.queue_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.queue_file, 'w') as f:
                json.dump(list(self.message_queue), f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save queued messages: {e}")
    
    async def _send_message_async(self, chat_id: str, message: str, parse_mode: str = 'Markdown') -> bool:
        """
        Send message asynchronously
        
        Args:
            chat_id: Telegram chat ID
            message: Message text
            parse_mode: Message formatting (Markdown or HTML)
            
        Returns:
            True if successful
        """
        for attempt in range(self.retry_attempts):
            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode=parse_mode
                )
                self.is_online = True
                self.last_connection_check = time.time()
                return True
                
            except RetryAfter as e:
                wait_time = e.retry_after
                self.logger.warning(f"Rate limited, waiting {wait_time}s")
                await asyncio.sleep(wait_time)
                
            except NetworkError as e:
                self.is_online = False
                self.logger.warning(f"Network error (attempt {attempt + 1}/{self.retry_attempts}): {e}")
                if attempt < self.retry_attempts - 1:
                    await asyncio.sleep(self.retry_delay)
                    
            except TelegramError as e:
                self.logger.error(f"Telegram error: {e}")
                return False
                
            except Exception as e:
                self.logger.error(f"Unexpected error sending message: {e}")
                return False
        
        return False
    
    def send_message(self, chat_id: str, message: str, parse_mode: str = 'Markdown') -> bool:
        """
        Send message (synchronous wrapper)
        
        Args:
            chat_id: Telegram chat ID
            message: Message text
            parse_mode: Message formatting
            
        Returns:
            True if successful
        """
        if not self.enabled:
            self.logger.debug("Telegram bot disabled, message not sent")
            return False
        
        try:
            # Create event loop if needed
            if self.loop is None or self.loop.is_closed():
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
            
            # Send message
            result = self.loop.run_until_complete(
                self._send_message_async(chat_id, message, parse_mode)
            )
            
            if result:
                self.logger.info(f"Message sent to {chat_id}")
            else:
                # Queue message for later
                self._queue_message(chat_id, message, parse_mode)
            
            return result
            
        except Exception as e:
            self.logger.error(f"Error in send_message: {e}")
            self._queue_message(chat_id, message, parse_mode)
            return False
            
    def _queue_message(self, chat_id: str, message: str, parse_mode: str):
        """Queue message for later delivery"""
        with self.queue_lock:
            queued_msg = {
                'chat_id': chat_id,
                'message': message,
                'parse_mode': parse_mode,
                'timestamp': time.time()
            }
            self.message_queue.append(queued_msg)
            self.logger.info(f"Message queued (queue size: {len(self.message_queue)})")
            self._save_queued_messages()
    
    def send_medication_reminder(self, medicine_name: str, dosage: int, time_str: str) -> bool:
        """
        Send medication reminder to patient
        
        Args:
            medicine_name: Name of medication
            dosage: Number of pills
            time_str: Scheduled time
            
        Returns:
            True if sent successfully
        """
        message = (
            f"?? *Medication Reminder*\n\n"
            f"?? Medicine: *{medicine_name}*\n"
            f"?? Dosage: *{dosage} pill(s)*\n"
            f"? Time: *{time_str}*\n\n"
            f"Please take your medication now."
        )
        
        return self.send_message(self.patient_chat_id, message)
    
    def send_dose_taken_confirmation(self, medicine_name: str, dosage: int) -> bool:
        """
        Send confirmation that dose was taken correctly
        
        Args:
            medicine_name: Name of medication
            dosage: Number of pills taken
            
        Returns:
            True if sent successfully
        """
        message = (
            f"? *Dose Confirmed*\n\n"
            f"?? {medicine_name}\n"
            f"?? {dosage} pill(s) taken correctly\n"
            f"? {time.strftime('%H:%M:%S')}\n\n"
            f"Great job staying on track!"
        )
        
        return self.send_message(self.patient_chat_id, message)
    
    def send_incorrect_dosage_alert(self, medicine_name: str, expected: int, actual: int) -> bool:
        """
        Send alert about incorrect dosage
        
        Args:
            medicine_name: Name of medication
            expected: Expected number of pills
            actual: Actual number taken
            
        Returns:
            True if sent successfully
        """
        # Alert to patient
        patient_message = (
            f"?? *Dosage Warning*\n\n"
            f"?? Medicine: {medicine_name}\n"
            f"Expected: {expected} pill(s)\n"
            f"Detected: {actual} pill(s)\n\n"
            f"Please verify your dosage."
        )
        
        # Alert to caregiver
        caregiver_message = (
            f"?? *Incorrect Dosage Alert*\n\n"
            f"Patient took incorrect dosage:\n"
            f"?? Medicine: {medicine_name}\n"
            f"?? Expected: {expected} pill(s)\n"
            f"?? Actual: {actual} pill(s)\n"
            f"? Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        patient_sent = self.send_message(self.patient_chat_id, patient_message)
        caregiver_sent = self.send_message(self.caregiver_chat_id, caregiver_message)
        
        return patient_sent and caregiver_sent
        
    def send_missed_dose_alert(self, medicine_name: str, scheduled_time: str, timeout_minutes: int) -> bool:
        """
        Send alert about missed dose
        
        Args:
            medicine_name: Name of medication
            scheduled_time: Scheduled time
            timeout_minutes: How long system waited
            
        Returns:
            True if sent successfully
        """
        # Alert to patient
        patient_message = (
            f"? *Missed Dose Alert*\n\n"
            f"?? Medicine: {medicine_name}\n"
            f"? Scheduled: {scheduled_time}\n\n"
            f"Please take your medication as soon as possible."
        )
        
        # Alert to caregiver
        caregiver_message = (
            f"?? *Missed Dose - Action Required*\n\n"
            f"Patient missed medication dose:\n"
            f"?? Medicine: {medicine_name}\n"
            f"? Scheduled: {scheduled_time}\n"
            f"?? Timeout: {timeout_minutes} minutes\n"
            f"?? Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"Please check on the patient."
        )
        
        patient_sent = self.send_message(self.patient_chat_id, patient_message)
        caregiver_sent = self.send_message(self.caregiver_chat_id, caregiver_message)
        
        return patient_sent and caregiver_sent
    
    def send_behavioral_alert(self, medicine_name: str, issue: str, details: Dict[str, Any]) -> bool:
        """
        Send alert about behavioral issues during intake
        
        Args:
            medicine_name: Name of medication
            issue: Type of issue (e.g., "excessive_coughing")
            details: Additional details
            
        Returns:
            True if sent successfully
        """
        # Format issue description
        issue_descriptions = {
            'excessive_coughing': '?? Excessive coughing detected',
            'no_swallow': '? No swallowing motion detected',
            'concerning': '?? Concerning behavioral patterns'
        }
        
        issue_text = issue_descriptions.get(issue, f'?? {issue}')
        
        # Alert to caregiver only (don't alarm patient)
        message = (
            f"?? *Behavioral Alert*\n\n"
            f"Issue during medication intake:\n"
            f"{issue_text}\n\n"
            f"?? Medicine: {medicine_name}\n"
            f"? Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"Details:\n"
        )
        
        # Add details
        if details.get('cough_count'):
            message += f"• Coughs detected: {details['cough_count']}\n"
        if details.get('swallow_count') is not None:
            message += f"• Swallows detected: {details['swallow_count']}\n"
        if details.get('compliance_status'):
            message += f"• Status: {details['compliance_status']}\n"
        
        return self.send_message(self.caregiver_chat_id, message)
        
    def send_daily_compliance_report(self, report_data: Dict[str, Any]) -> bool:
        """
        Send daily compliance report to caregiver
        
        Args:
            report_data: Compliance statistics
            
        Returns:
            True if sent successfully
        """
        date_str = time.strftime('%Y-%m-%d')
        
        message = (
            f"?? *Daily Compliance Report*\n"
            f"?? Date: {date_str}\n\n"
        )
        
        # Add statistics
        total_doses = report_data.get('total_scheduled', 0)
        taken_correctly = report_data.get('taken_correctly', 0)
        taken_incorrectly = report_data.get('taken_incorrectly', 0)
        missed = report_data.get('missed', 0)
        
        compliance_rate = (taken_correctly / total_doses * 100) if total_doses > 0 else 0
        
        message += f"? Taken correctly: {taken_correctly}/{total_doses}\n"
        message += f"?? Incorrect dosage: {taken_incorrectly}\n"
        message += f"? Missed: {missed}\n"
        message += f"?? Compliance rate: {compliance_rate:.1f}%\n\n"
        
        # Behavioral summary
        if report_data.get('behavioral_issues', 0) > 0:
            message += f"?? Behavioral issues: {report_data['behavioral_issues']}\n"
        
        # Overall status
        if compliance_rate >= 90:
            message += "\n? Excellent compliance!"
        elif compliance_rate >= 70:
            message += "\n?? Good compliance"
        else:
            message += "\n?? Needs attention"
        
        return self.send_message(self.caregiver_chat_id, message)
    
    def _process_queue(self):
        """Process queued messages (runs in background thread)"""
        self.logger.info("Starting message queue processor")
        
        while self.queue_processor_running:
            try:
                # Check if we have queued messages
                if len(self.message_queue) > 0:
                    with self.queue_lock:
                        # Try to send oldest message
                        msg = self.message_queue[0]
                        
                        success = self.send_message(
                            msg['chat_id'],
                            msg['message'],
                            msg['parse_mode']
                        )
                        
                        if success:
                            # Remove from queue
                            self.message_queue.popleft()
                            self._save_queued_messages()
                            self.logger.info(
                                f"Queued message sent ({len(self.message_queue)} remaining)"
                            )
                
                # Sleep before next check
                time.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                self.logger.error(f"Error in queue processor: {e}")
                time.sleep(60)
        
        self.logger.info("Message queue processor stopped")
        
