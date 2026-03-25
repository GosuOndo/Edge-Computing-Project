"""
Smart Medication System - Tag Runtime Service

Listens for live tag scan MQTT messages and provides two query modes:

1. PASSIVE (integrated mode - tag reader under scale):
   - get_tag_within_window(window_seconds)
   - verify_coincident_tag(weight_event_timestamp, ...)

2. ACTIVE (legacy / non-integrated mode):
   - wait_for_matching_tag(...)

Scan control:
   - start_scanning()  - sends {"command": "start_scan"} to the firmware.
                         Call at the start of each onboarding slot and when
                         the bottle is lifted during daily use.
   - stop_scanning()   - sends {"command": "stop_scan"} to the firmware.
                         Call after onboarding completes and after each
                         identity verification cycle.
"""

import json
import time
from threading import Lock
from typing import Optional, Dict, Any, List

import paho.mqtt.client as mqtt

from raspberry_pi.modules.tag_manager import TagManager


class TagRuntimeService:
    """Waits for live tag scans over MQTT and resolves them against the database."""

    def __init__(
        self,
        mqtt_config: dict,
        database,
        logger,
        topic: str,
        command_topic: str = "medication/tag/command/tag_reader_1",
        command_topics: Optional[Dict[str, str]] = None,
        station_to_reader: Optional[Dict[str, str]] = None,
    ):
        self.mqtt_config = mqtt_config
        self.database = database
        self.logger = logger
        self.topic = topic

        # Per-station command topics: station_id -> MQTT command topic.
        # Falls back to the legacy single command_topic when not provided.
        if command_topics:
            self._station_command_topics: Dict[str, str] = command_topics
        else:
            self._station_command_topics = {"_default": command_topic}

        # station_id -> reader_id mapping (used to filter per-reader scan buffers)
        self._station_to_reader: Dict[str, str] = station_to_reader or {}
        # Reverse map: reader_id -> station_id (built from station_to_reader)
        self._reader_to_station: Dict[str, str] = {
            v: k for k, v in self._station_to_reader.items()
        }

        self.tag_manager = TagManager(logger)

        self.client = None
        self.connected = False

        # Latest scan from any reader (backward-compat)
        self.latest_scan: Optional[Dict[str, Any]] = None
        # Per-reader scan buffers: reader_id -> scan dict
        self._latest_scans_by_reader: Dict[str, Optional[Dict[str, Any]]] = {}
        self.latest_scan_lock = Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Start MQTT listener for tag scan events."""
        if self.client is not None:
            return

        self.client = mqtt.Client(
            client_id="pi_tag_runtime_service",
            protocol=mqtt.MQTTv311
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        self.client.connect(
            self.mqtt_config["broker_host"],
            self.mqtt_config["broker_port"],
            self.mqtt_config.get("keepalive", 60)
        )
        self.client.loop_start()

        start = time.time()
        while not self.connected and (time.time() - start) < 5:
            time.sleep(0.1)

        if self.connected:
            self.logger.info("Tag runtime service started")
        else:
            self.logger.warning(
                "Tag runtime service did not confirm MQTT connection within 5s"
            )

    def stop(self):
        """Stop MQTT listener."""
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            self.client = None
            self.connected = False
            self.logger.info("Tag runtime service stopped")

    # ------------------------------------------------------------------
    # MQTT callbacks
    # ------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            client.subscribe(self.topic, qos=1)
            self.logger.info(f"Tag runtime subscribed to {self.topic}")
        else:
            self.connected = False
            self.logger.error(f"Tag runtime MQTT connection failed rc={rc}")

    def _on_message(self, client, userdata, msg):
        try:
            payload_text = msg.payload.decode("utf-8")
            scan_msg = json.loads(payload_text)

            reader_id = scan_msg.get("reader_id", "unknown")
            scan_entry = {
                "received_at": time.time(),
                "scan_msg": scan_msg
            }

            with self.latest_scan_lock:
                self.latest_scan = scan_entry
                self._latest_scans_by_reader[reader_id] = scan_entry

            self.logger.info(
                f"Live tag scan received: UID={scan_msg.get('tag_uid')} "
                f"reader={reader_id}"
            )

        except Exception as e:
            self.logger.error(f"Failed to process live tag scan: {e}")

    # ------------------------------------------------------------------
    # Scan control  (NEW)
    # ------------------------------------------------------------------

    def _send_scan_command(self, command: str, station_id: Optional[str] = None):
        """
        Publish a scan control command to the firmware.

        Args:
            command:    "start_scan" or "stop_scan"
            station_id: If provided, only the reader for that station is
                        commanded.  If None, all configured readers are
                        commanded simultaneously.
        """
        if not self.client or not self.connected:
            self.logger.warning(
                f"Cannot send {command}: tag runtime service not connected"
            )
            return

        payload = json.dumps({"command": command})

        if station_id and station_id in self._station_command_topics:
            topics: List[str] = [self._station_command_topics[station_id]]
        else:
            topics = list(self._station_command_topics.values())

        for topic in topics:
            result = self.client.publish(topic, payload, qos=1)
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self.logger.info(
                    f"Tag scan command sent: {command} -> {topic}"
                )
            else:
                self.logger.error(
                    f"Failed to send tag scan command {command} to {topic} "
                    f"(rc={result.rc})"
                )

    def start_scanning(self, station_id: Optional[str] = None):
        """
        Enable RF polling on the tag reader firmware.

        Pass station_id to target only that station's reader; omit to
        broadcast to all readers (e.g. during global reset / onboarding).
        """
        self._send_scan_command("start_scan", station_id)

    def stop_scanning(self, station_id: Optional[str] = None):
        """
        Disable RF polling on the tag reader firmware.

        Pass station_id to target only that station's reader; omit to
        broadcast to all readers.
        """
        self._send_scan_command("stop_scan", station_id)

    # ------------------------------------------------------------------
    # Scan buffer access
    # ------------------------------------------------------------------

    def _reader_id_for_station(self, station_id: Optional[str]) -> Optional[str]:
        """Return the reader_id associated with station_id, or None if unknown."""
        return self._station_to_reader.get(station_id) if station_id else None

    def clear_latest_scan(self, station_id: Optional[str] = None):
        """
        Discard the stored scan so the next query starts fresh.

        If station_id is provided, only that station's reader buffer is cleared
        (plus the global latest_scan if it came from that reader).
        """
        reader_id = self._reader_id_for_station(station_id)
        with self.latest_scan_lock:
            if reader_id:
                self._latest_scans_by_reader.pop(reader_id, None)
                # Clear global buffer only when it came from the same reader
                if (
                    self.latest_scan
                    and self.latest_scan.get("scan_msg", {}).get("reader_id")
                    == reader_id
                ):
                    self.latest_scan = None
            else:
                self.latest_scan = None
                self._latest_scans_by_reader.clear()

    def get_latest_scan(self, station_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Return the stored scan dict (or None) without removing it.

        If station_id is provided, return only the scan from that station's
        reader.  Otherwise return the most recent scan from any reader.
        """
        reader_id = self._reader_id_for_station(station_id)
        with self.latest_scan_lock:
            if reader_id:
                entry = self._latest_scans_by_reader.get(reader_id)
                return entry.copy() if entry else None
            return self.latest_scan.copy() if self.latest_scan else None

    # ------------------------------------------------------------------
    # Passive query methods (integrated tag-under-scale mode)
    # ------------------------------------------------------------------

    def get_tag_within_window(
        self,
        window_seconds: float = 10.0,
        station_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Return the most recent scan if it arrived within the last
        window_seconds. Returns None if no scan or scan is older.

        If station_id is provided, only that station's reader is checked.
        """
        reader_id = self._reader_id_for_station(station_id)
        with self.latest_scan_lock:
            if reader_id:
                entry = self._latest_scans_by_reader.get(reader_id)
            else:
                entry = self.latest_scan
            if not entry:
                return None
            age = time.time() - entry.get("received_at", 0)
            if age <= window_seconds:
                return entry.copy()
            return None

    def verify_coincident_tag(
        self,
        weight_event_timestamp: float,
        expected_medicine_id: Optional[str],
        expected_station_id: Optional[str],
        window_seconds: float = 15.0
    ) -> Dict[str, Any]:
        """
        Verify that a tag scan arrived near a specific weight event.

        Acceptance window:
            [weight_event_timestamp - window_seconds,
             weight_event_timestamp + 3.0]

        When expected_station_id is provided and a station->reader mapping
        exists, only the scan from that station's dedicated reader is checked.
        """
        reader_id = self._reader_id_for_station(expected_station_id)
        with self.latest_scan_lock:
            if reader_id:
                entry = self._latest_scans_by_reader.get(reader_id)
            else:
                entry = self.latest_scan

            if not entry:
                return {
                    "success": False,
                    "reason": "No tag scan on record"
                }

            scan_time    = entry.get("received_at", 0)
            window_start = weight_event_timestamp - window_seconds
            window_end   = weight_event_timestamp + 3.0

            if not (window_start <= scan_time <= window_end):
                age = time.time() - scan_time
                return {
                    "success": False,
                    "reason": (
                        f"Tag scan is too old: {age:.1f}s ago "
                        f"(window={window_seconds}s before / 3s after event)"
                    )
                }

            scan_msg = entry["scan_msg"]
            tag_uid  = scan_msg.get("tag_uid")

        # Resolve medicine record: database first, tag payload fallback
        db_record = None
        if tag_uid:
            db_record = self.database.get_registered_medicine_by_tag_uid(tag_uid)

        if db_record is None:
            db_record = self.tag_manager.build_record_from_scan(scan_msg)

        if db_record is None:
            return {
                "success": False,
                "reason": "Could not parse tag payload into a medicine record"
            }

        verify_result = self.tag_manager.verify_scan_against_expected(
            db_record,
            expected_medicine_id=expected_medicine_id,
            expected_station_id=expected_station_id
        )

        if verify_result["match"]:
            self.logger.info(
                f"Coincident tag verified: {db_record.get('medicine_id')} "
                f"@ {db_record.get('station_id')}"
            )
            return {
                "success": True,
                "method": "tag_integrated",
                "tag_uid": tag_uid,
                "record": db_record,
                "verification": verify_result
            }

        return {
            "success": False,
            "reason": verify_result.get("reason", "Tag/medicine mismatch"),
            "record": db_record,
            "verification": verify_result
        }

    # ------------------------------------------------------------------
    # Active (blocking) method - legacy / non-integrated fallback
    # ------------------------------------------------------------------

    def wait_for_matching_tag(
        self,
        expected_medicine_id: str,
        expected_station_id: str,
        max_attempts: int = 3,
        attempt_timeout_seconds: int = 6
    ) -> Dict[str, Any]:
        """
        Block until a matching live tag scan is received.
        Clears the scan buffer at the start so stale scans are ignored.
        """
        self.clear_latest_scan()
        seen_uids: set = set()

        for attempt in range(1, max_attempts + 1):
            self.logger.info(
                f"Waiting for tag attempt {attempt}/{max_attempts} "
                f"(timeout={attempt_timeout_seconds}s)"
            )

            start = time.time()

            while (time.time() - start) < attempt_timeout_seconds:
                latest = self.get_latest_scan()
                if not latest:
                    time.sleep(0.1)
                    continue

                scan_msg = latest["scan_msg"]
                tag_uid  = scan_msg.get("tag_uid")

                if tag_uid and tag_uid in seen_uids:
                    time.sleep(0.1)
                    continue

                if tag_uid:
                    seen_uids.add(tag_uid)

                db_record = None
                if tag_uid:
                    db_record = self.database.get_registered_medicine_by_tag_uid(tag_uid)

                if db_record is None:
                    db_record = self.tag_manager.build_record_from_scan(scan_msg)

                verify_result = self.tag_manager.verify_scan_against_expected(
                    db_record,
                    expected_medicine_id=expected_medicine_id,
                    expected_station_id=expected_station_id
                )

                if verify_result["match"]:
                    return {
                        "success": True,
                        "method": "tag",
                        "attempts_used": attempt,
                        "tag_uid": tag_uid,
                        "record": db_record,
                        "verification": verify_result
                    }

                self.logger.warning(
                    f"Tag mismatch on attempt {attempt}: "
                    f"{verify_result.get('reason')}"
                )
                self.clear_latest_scan()
                time.sleep(0.1)

        return {
            "success": False,
            "method": "tag",
            "attempts_used": max_attempts,
            "reason": "No matching tag scan received within retry limit"
        }
