"""
Smart Medication System - Tag Runtime Service

Listens for live tag scan MQTT messages and provides
blocking wait helpers for runtime verification.
"""

import json
import time
from threading import Lock
from typing import Optional, Dict, Any

import paho.mqtt.client as mqtt

from raspberry_pi.modules.tag_manager import TagManager

class TagRuntimeService:
    """Waits for live tag scans over MQTT and resolves them against the database."""

    def __init__(self, mqtt_config: dict, database, logger, topic: str):
        self.mqtt_config = mqtt_config
        self.database = database
        self.logger = logger
        self.topic = topic

        self.tag_manager = TagManager(logger)

        self.client = None
        self.connected = False

        self.latest_scan = None
        self.latest_scan_lock = Lock()

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
            self.logger.warning("Tag runtime service did not confirm MQTT connection")

    def stop(self):
        """Stop MQTT listener."""
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            self.client = None
            self.connected = False
            self.logger.info("Tag runtime service stopped")

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            client.subscribe(self.topic, qos=1)
            self.logger.info(f"Tag runtime subscribed to {self.topic}")
        else:
            self.connected = False
            self.logger.error(f"Tag runtime MQTT connection failed with rc={rc}")

    def _on_message(self, client, userdata, msg):
        try:
            payload_text = msg.payload.decode("utf-8")
            scan_msg = json.loads(payload_text)

            with self.latest_scan_lock:
                self.latest_scan = {
                    "received_at": time.time(),
                    "scan_msg": scan_msg
                }

            self.logger.info(
                f"Live tag scan received: UID={scan_msg.get('tag_uid')} "
                f"reader={scan_msg.get('reader_id')}"
            )

        except Exception as e:
            self.logger.error(f"Failed to process live tag scan: {e}")

    def clear_latest_scan(self):
        with self.latest_scan_lock:
            self.latest_scan = None

    def get_latest_scan(self) -> Optional[Dict[str, Any]]:
        with self.latest_scan_lock:
            return self.latest_scan.copy() if self.latest_scan else None

    def wait_for_matching_tag(
        self,
        expected_medicine_id: str,
        expected_station_id: str,
        max_attempts: int = 3,
        attempt_timeout_seconds: int = 6
    ) -> Dict[str, Any]:
        """
        Wait for a matching live tag scan.

        Returns a structured result dict.
        """
        self.clear_latest_scan()

        seen_uids = set()

        for attempt in range(1, max_attempts + 1):
            self.logger.info(
                f"Waiting for tag attempt {attempt}/{max_attempts} "
                f"(timeout={attempt_timeout_seconds}s)"
            )

            start = time.time()

            while (time.time() - start) < attempt_timeout_seconds:
                latest = self.get_latest_scan()
                if latest:
                    scan_msg = latest["scan_msg"]
                    tag_uid = scan_msg.get("tag_uid")

                    # prevent processing the same tag event repeatedly in same attempt loop
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
                        f"Tag mismatch on attempt {attempt}: {verify_result.get('reason')}"
                    )
                    self.clear_latest_scan()

                time.sleep(0.1)

        return {
            "success": False,
            "method": "tag",
            "attempts_used": max_attempts,
            "reason": "No matching tag scan received within retry limit"
        }
