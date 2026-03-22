"""
Telegram Bot - Sends medication reminders and alerts to patient and caregiver.
Queues messages locally when the network is unavailable and retries automatically.

Offline behaviour: if a send fails, the message is appended to a local JSON file
(data/telegram_queue.json). A background thread retries the queue every 30 seconds
so no alert is permanently lost during a network outage.
"""

import asyncio
import time
import json
from pathlib import Path
from collections import deque
from threading import Thread, Lock
from typing import Dict, Any, List, Optional

from telegram import Bot
from telegram.error import TelegramError, NetworkError, RetryAfter


class TelegramBot:

    def __init__(self, config: dict, logger):
        self.config = config
        self.logger = logger

        self.enabled = config.get('enabled', True)
        self.bot_token = config.get('bot_token', '')
        # Separate chat IDs allow patient and caregiver to receive different messages
        self.patient_chat_id = config.get('patient_chat_id', '')
        self.caregiver_chat_id = config.get('caregiver_chat_id', '')
        self.retry_attempts = config.get('retry_attempts', 3)
        self.retry_delay = config.get('retry_delay_seconds', 5)

        # Bot instance used by the async send method
        self.bot = None
        # A single event loop is reused across all synchronous send_message calls
        self.loop = None

        # In-memory queue backed by a JSON file for persistence across reboots
        self.message_queue: deque = deque(maxlen=100)
        self.queue_lock = Lock()
        self.queue_file = Path('data/telegram_queue.json')

        self.is_online = False
        self.last_connection_check = 0

        self.queue_processor_running = False
        self.queue_processor_thread: Optional[Thread] = None

        if self.enabled:
            self._validate_config()
            self._initialize_bot()
            # Reload any messages that were queued before the last shutdown
            self._load_queued_messages()

        self.logger.info(f"Telegram bot initialized (enabled: {self.enabled})")

    def _validate_config(self):
        """Fail fast on startup if the token is still the placeholder value."""
        if not self.bot_token or 'YOUR_BOT_TOKEN' in self.bot_token:
            raise ValueError(
                "Telegram bot token not configured. Set bot_token in config.yaml."
            )
        if not self.patient_chat_id:
            self.logger.warning("Patient chat ID not configured")
        if not self.caregiver_chat_id:
            self.logger.warning("Caregiver chat ID not configured")

    def _initialize_bot(self):
        try:
            self.bot = Bot(token=self.bot_token)
            self.logger.info("Telegram bot instance created")
        except Exception as e:
            self.logger.error(f"Failed to initialize Telegram bot: {e}")
            raise
            
    def _load_queued_messages(self):
        """Restore unsent messages from the previous session."""
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
        """Persist the current queue to disk so messages survive a reboot."""
        try:
            self.queue_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.queue_file, 'w') as f:
                json.dump(list(self.message_queue), f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save queued messages: {e}")

    async def _send_message_async(self, chat_id: str, message: str, parse_mode: str = 'Markdown') -> bool:
        """
        Low-level async sender with per-attempt retry logic.
        RetryAfter means Telegram is rate-limiting us - we honour the wait time it provides.
        NetworkError means the Pi has no internet - we wait retry_delay seconds between attempts.
        Any other TelegramError (e.g. bad chat_id) is a permanent failure, no point retrying.
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
                # Telegram told us exactly how long to wait
                await asyncio.sleep(e.retry_after)

            except NetworkError as e:
                self.is_online = False
                self.logger.warning(f"Network error attempt {attempt + 1}: {e}")
                if attempt < self.retry_attempts - 1:
                    await asyncio.sleep(self.retry_delay)

            except TelegramError as e:
                # Permanent Telegram-side error (wrong token, banned bot, etc.)
                self.logger.error(f"Telegram error: {e}")
                return False

            except Exception as e:
                self.logger.error(f"Unexpected error sending message: {e}")
                return False

        return False

    def send_message(self, chat_id: str, message: str, parse_mode: str = 'Markdown') -> bool:
        """
        Public synchronous wrapper around the async sender.
        Reuses a single event loop rather than creating a new one per call,
        which avoids thread safety issues with asyncio on Python 3.10+.
        If sending fails, the message is queued for later delivery.
        """
        if not self.enabled:
            return False
        if not chat_id:
            self.logger.warning("send_message called with empty chat_id")
            return False

        try:
            if self.loop is None or self.loop.is_closed():
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)

            result = self.loop.run_until_complete(
                self._send_message_async(chat_id, message, parse_mode)
            )

            if not result:
                self._queue_message(chat_id, message, parse_mode)

            return result

        except Exception as e:
            self.logger.error(f"Error in send_message: {e}")
            self._queue_message(chat_id, message, parse_mode)
            return False
            
    def _queue_message(self, chat_id: str, message: str, parse_mode: str):
        """Add a failed message to the offline queue and persist it immediately."""
        with self.queue_lock:
            self.message_queue.append({
                'chat_id': chat_id,
                'message': message,
                'parse_mode': parse_mode,
                'timestamp': time.time()
            })
            self.logger.info(f"Message queued (queue size: {len(self.message_queue)})")
            self._save_queued_messages()

    @staticmethod
    def _escape_md(text: str) -> str:
        """
        Escape Telegram Markdown v1 special characters in variable content.
        Telegram treats _ as italic and * as bold markers, so any variable field
        (medicine name, station ID, etc.) that contains these characters will
        break the parser unless they are escaped with a backslash.
        Characters that need escaping: _ * ` [
        """
        for ch in ('_', '*', '`', '['):
            text = text.replace(ch, f'\\{ch}')
        return text

    def send_medication_reminder(self, medicine_name: str, dosage: int, time_str: str) -> bool:
        name = self._escape_md(medicine_name)
        message = (
            f"*MEDICATION REMINDER*\n\n"
            f"Medicine: *{name}*\n"
            f"Dosage: *{dosage} pill(s)*\n"
            f"Scheduled time: *{time_str}*\n\n"
            f"Please take your medication now."
        )
        return self.send_message(self.patient_chat_id, message)

    def send_dose_taken_confirmation(self, medicine_name: str, dosage: int) -> bool:
        name = self._escape_md(medicine_name)
        taken_at = time.strftime('%H:%M:%S')
        message = (
            f"*DOSE CONFIRMED*\n\n"
            f"Medicine: {name}\n"
            f"Amount: {dosage} pill(s)\n"
            f"Confirmed at: {taken_at}\n\n"
            f"Great job staying on track!"
        )
        return self.send_message(self.patient_chat_id, message)

    def send_incorrect_dosage_alert(self, medicine_name: str, expected: int, actual: int) -> bool:
        name = self._escape_md(medicine_name)
        patient_msg = (
            f"*DOSAGE WARNING*\n\n"
            f"Medicine: {name}\n"
            f"Expected: {expected} pill(s)\n"
            f"Detected: {actual} pill(s)\n\n"
            f"Please check your dosage and try again."
        )
        caregiver_msg = (
            f"*ALERT - Incorrect Dosage*\n\n"
            f"Patient took incorrect dosage:\n"
            f"Medicine: {name}\n"
            f"Expected: {expected} pill(s)\n"
            f"Actual taken: {actual} pill(s)\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        patient_sent = self.send_message(self.patient_chat_id, patient_msg)
        caregiver_sent = self.send_message(self.caregiver_chat_id, caregiver_msg)
        return patient_sent and caregiver_sent
        
    def send_missed_dose_alert(self, medicine_name: str, scheduled_time: str, timeout_minutes: int) -> bool:
        name = self._escape_md(medicine_name)
        patient_msg = (
            f"*MISSED DOSE*\n\n"
            f"Medicine: {name}\n"
            f"Scheduled at: {scheduled_time}\n\n"
            f"Please take your medication as soon as possible."
        )
        caregiver_msg = (
            f"*ALERT - Missed Dose*\n\n"
            f"Patient missed a medication dose:\n"
            f"Medicine: {name}\n"
            f"Scheduled at: {scheduled_time}\n"
            f"No action after: {timeout_minutes} minutes\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"Please check on the patient."
        )
        patient_sent = self.send_message(self.patient_chat_id, patient_msg)
        caregiver_sent = self.send_message(self.caregiver_chat_id, caregiver_msg)
        return patient_sent and caregiver_sent

    def send_behavioral_alert(self, medicine_name: str, issue: str, details: Dict[str, Any]) -> bool:
        name = self._escape_md(medicine_name)
        issue_labels = {
            'excessive_coughing': 'Excessive coughing detected',
            'no_swallow':         'No swallowing motion detected',
            'concerning':         'Concerning behavior during intake',
        }
        issue_text = issue_labels.get(issue, f'Behavioral issue: {self._escape_md(issue)}')

        message = (
            f"*ALERT - Behavioral Issue*\n\n"
            f"Issue during medication intake:\n"
            f"{issue_text}\n\n"
            f"Medicine: {name}\n"
            f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"Details:\n"
        )

        if details.get('cough_count'):
            message += f"- Coughs detected: {details['cough_count']}\n"
        if details.get('swallow_count') is not None:
            message += f"- Swallows detected: {details['swallow_count']}\n"
        if details.get('compliance_status'):
            message += f"- Status: {self._escape_md(str(details['compliance_status']))}\n"

        return self.send_message(self.caregiver_chat_id, message)

    def send_registration_confirmation(
        self,
        medicine_name: str,
        station_id: str,
        dosage: int,
        schedule_times: List[str]
    ) -> bool:
        # station_id contains underscores (e.g. station_1) which must be escaped
        name = self._escape_md(medicine_name)
        sid  = self._escape_md(station_id)
        times_str = ', '.join(schedule_times) if schedule_times else 'Not set'
        message = (
            f"*MEDICINE REGISTERED*\n\n"
            f"Medicine: *{name}*\n"
            f"Station: {sid}\n"
            f"Dosage: {dosage} pill(s) per dose\n"
            f"Schedule: {times_str}\n\n"
            f"The system will send reminders at the scheduled times."
        )
        patient_sent = self.send_message(self.patient_chat_id, message)
        caregiver_sent = self.send_message(self.caregiver_chat_id, message)
        return patient_sent and caregiver_sent
        
    def send_daily_compliance_report(self, report_data: Dict[str, Any]) -> bool:
        total = report_data.get('total_scheduled', 0)
        correct = report_data.get('taken_correctly', 0)
        incorrect = report_data.get('taken_incorrectly', 0)
        missed = report_data.get('missed', 0)
        rate = (correct / total * 100) if total > 0 else 0.0

        if rate >= 90:
            status = "Excellent compliance"
        elif rate >= 70:
            status = "Good compliance"
        else:
            status = "Needs attention"

        message = (
            f"*DAILY COMPLIANCE REPORT*\n"
            f"Date: {time.strftime('%Y-%m-%d')}\n\n"
            f"Taken correctly: {correct}/{total}\n"
            f"Incorrect dosage: {incorrect}\n"
            f"Missed: {missed}\n"
            f"Compliance rate: {rate:.1f}%\n\n"
            f"Status: {status}"
        )

        if report_data.get('behavioral_issues', 0) > 0:
            message += f"\nBehavioral issues noted: {report_data['behavioral_issues']}"

        return self.send_message(self.caregiver_chat_id, message)

    def _process_queue(self):
        """
        Background thread that retries queued messages every 30 seconds.
        Processes one message per cycle so a large backlog does not cause
        a burst of API calls that triggers rate limiting.
        Sleeps for 60 seconds after an unexpected error to avoid a tight error loop.
        """
        self.logger.info("Message queue processor started")
        while self.queue_processor_running:
            try:
                if len(self.message_queue) > 0:
                    with self.queue_lock:
                        if self.message_queue:
                            msg = self.message_queue[0]
                            success = self.send_message(
                                msg['chat_id'],
                                msg['message'],
                                msg.get('parse_mode', 'Markdown')
                            )
                            if success:
                                self.message_queue.popleft()
                                self._save_queued_messages()
                                self.logger.info(
                                    f"Queued message delivered ({len(self.message_queue)} remaining)"
                                )
                time.sleep(30)
            except Exception as e:
                self.logger.error(f"Queue processor error: {e}")
                time.sleep(60)

        self.logger.info("Message queue processor stopped")
        
    def start_queue_processor(self):
        """Launch the background retry thread. Called once during system startup."""
        if self.queue_processor_running:
            return
        self.queue_processor_running = True
        self.queue_processor_thread = Thread(target=self._process_queue, daemon=True)
        self.queue_processor_thread.start()
        self.logger.info("Queue processor started")

    def stop_queue_processor(self):
        """Signal the retry thread to stop and wait up to 5 seconds for it to exit."""
        self.queue_processor_running = False
        if self.queue_processor_thread:
            self.queue_processor_thread.join(timeout=5)
        self.logger.info("Queue processor stopped")

    def get_queue_size(self) -> int:
        """Returns the number of messages currently waiting to be delivered."""
        return len(self.message_queue)

    def is_connected(self) -> bool:
        """
        True if the last successful send was within the past 5 minutes.
        Used by the main system to decide whether to surface a connectivity warning.
        """
        if not self.is_online:
            return False
        return (time.time() - self.last_connection_check) < 300

    def cleanup(self):
        """Stop the retry thread and persist any remaining queued messages before exit."""
        self.stop_queue_processor()
        self._save_queued_messages()
        if self.loop and not self.loop.is_closed():
            self.loop.close()
        self.logger.info("Telegram bot cleanup complete")
