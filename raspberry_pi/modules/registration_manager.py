"""
Smart Medication System - Registration Manager

Handles the one-time medicine registration flow for each station.

Since the RC522 tag reader is physically mounted under the scale platform,
registration is triggered by simply placing the medicine bottle on the station.
The bottle bottom carries an RFID sticker. When the bottle is placed:
  - The tag reader reads the sticker immediately on contact
  - The scale reads the weight (takes a few more seconds to stabilise)

The correct placement sequence is therefore:
  1. Patient places bottle on station
  2. Tag is scanned immediately (Phase A has not finished yet)
  3. Weight stabilises a few seconds later (Phase A completes)
  4. Phase B checks: was there a tag scan since the bottle appeared? YES -> done

This means clear_latest_scan() must NOT be called between Phase A and Phase B.
Instead we record the time when the bottle weight first appeared above threshold
and accept any scan that arrived from that moment onward.

Tag payload format (written via medication_tag_write_read_test.ino):
    ID=M001;P=P001;N=ASPIRIN100;D=2;T=08,20;M=AF;S=1
"""

import time
from typing import Optional, List, Dict, Any


class RegistrationManager:
    """Handles first-time medicine registration for each configured station."""

    def __init__(
        self,
        config: dict,
        weight_manager,
        tag_runtime_service,
        database,
        display,
        audio,
        telegram,
        logger
    ):
        self.config = config
        self.weight_manager = weight_manager
        self.tag_runtime_service = tag_runtime_service
        self.database = database
        self.display = display
        self.audio = audio
        self.telegram = telegram
        self.logger = logger

        reg_cfg = config.get("registration", {})
        self.enabled = reg_cfg.get("enabled", True)
        self.timeout_seconds = reg_cfg.get("timeout_seconds", 120)
        self.min_bottle_weight_g = reg_cfg.get("min_bottle_weight_g", 5.0)
        # After bottle is detected on scale, how long to wait for tag scan
        self.tag_wait_seconds = reg_cfg.get("tag_wait_seconds", 30.0)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    
    def stations_needing_registration(self) -> List[str]:
        """Return list of station_ids that have no active registered medicine."""
        unregistered = []
        for station_id in self.weight_manager.station_configs:
            record = self.database.get_registered_medicine_by_station(station_id)
            if not record:
                unregistered.append(station_id)
        return unregistered

    def run_registration_if_needed(self) -> bool:
        """
        Check every configured station and run registration for any that are
        unregistered. Called once during system startup before the main loop.
        Returns True if all stations are now registered and ready.
        """
        if not self.enabled:
            self.logger.info("Registration disabled in config, skipping")
            return True

        unregistered = self.stations_needing_registration()
        if not unregistered:
            self.logger.info(
                f"All {len(self.weight_manager.station_configs)} station(s) "
                f"already registered. Skipping registration."
            )
            return True

        self.logger.info(f"Stations needing registration: {unregistered}")
        all_ok = True
        for station_id in unregistered:
            ok = self._register_station(station_id)
            if not ok:
                self.logger.error(f"Registration failed for {station_id}")
                all_ok = False

        return all_ok

    # ------------------------------------------------------------------
    # Core registration flow
    # ------------------------------------------------------------------

    def _register_station(self, station_id: str) -> bool:
        """
        Registration flow for a single station.

        Phase A - Wait for stable weight:
            Poll until weight > min_bottle_weight_g AND stable.
            Record bottle_detected_at the moment weight first exceeds threshold
            (even before it stabilises) so we capture any tag scan that arrives
            as soon as the bottle touches the reader.

        Phase B - Find tag scan:
            Accept any scan that arrived at or after bottle_detected_at.
            Do NOT clear the scan buffer between phases.
            If no scan found yet, wait up to tag_wait_seconds for a new one.

        Phase C - Build record, save, capture baseline.
        """
        self.logger.info(f"Starting registration for {station_id}")
        print(f"\n  [REGISTRATION] Station: {station_id}")
        print(f"  [REG] Place the medicine bottle on the station.")
        print(f"  [REG] The tag is read on contact; weight needs a few seconds to stabilise.")

        if self.display:
            self.display.show_registration_screen(
                station_id, "Place medicine bottle on scale"
            )
        if self.audio:
            self.audio.speak(
                "Please place the medicine bottle on the scale to register."
            )

        deadline = time.time() + self.timeout_seconds

        # ---- Phase A: wait for stable weight ----
        print(f"  [REG] Phase A: waiting for stable bottle (>{self.min_bottle_weight_g}g)...")
        stable_weight = None
        bottle_detected_at = None  # when weight first crossed threshold

        while time.time() < deadline:
            if self.display:
                self.display.update()

            status = self.weight_manager.get_station_status(station_id)

            if not status.get("connected"):
                self._update_screen(station_id, "Waiting for scale connection...")
                time.sleep(0.5)
                continue

            weight_g = float(status.get("weight_g") or 0.0)
            is_stable = status.get("stable", False)

            if weight_g < self.min_bottle_weight_g:
                if bottle_detected_at is not None:
                    # Bottle was removed, reset
                    bottle_detected_at = None
                    print("  [REG] Bottle removed, waiting again...")
                self._update_screen(station_id, "Place full bottle on scale...")
                time.sleep(0.3)
                continue

            # Bottle is present - record the moment it first appeared
            if bottle_detected_at is None:
                bottle_detected_at = time.time()
                print(f"  [REG] Bottle detected ({weight_g:.1f}g), waiting to stabilise...")
                self._update_screen(station_id, f"Bottle detected ({weight_g:.1f}g), stabilising...")

            if not is_stable:
                time.sleep(0.3)
                continue

            # Stable bottle confirmed
            stable_weight = weight_g
            print(f"  [REG] Phase A done: stable at {stable_weight:.2f}g "
                  f"(bottle appeared {time.time() - bottle_detected_at:.1f}s ago)")
            break
        else:
            self._timeout(station_id)
            return False
            
        # ---- Phase B: find tag scan ----
        # Accept any scan that arrived from the moment the bottle appeared.
        # A small lookback buffer (2s before bottle_detected_at) handles the
        # case where the reader fires a scan fractionally before the weight
        # sensor reports above threshold.
        lookback_from = bottle_detected_at - 2.0

        print(f"  [REG] Phase B: looking for tag scan since bottle appeared...")
        self._update_screen(station_id, "Reading tag... (tag scanned on contact)")

        scan_msg = None
        tag_deadline = time.time() + self.tag_wait_seconds
        dots = 0

        while time.time() < tag_deadline and time.time() < deadline:
            if self.display:
                self.display.update()

            latest = self.tag_runtime_service.get_latest_scan()
            if latest and latest.get("received_at", 0) >= lookback_from:
                scan_msg = latest["scan_msg"]
                tag_age = time.time() - latest["received_at"]
                tag_uid = scan_msg.get("tag_uid", "?")
                print(f"  [REG] Phase B done: tag UID={tag_uid} "
                      f"(arrived {tag_age:.1f}s ago)")
                break

            dots += 1
            if dots % 10 == 0:
                elapsed_b = time.time() - (tag_deadline - self.tag_wait_seconds)
                print(f"  [REG] Waiting for tag scan... ({elapsed_b:.0f}s). "
                      "Hold bottle still on station.")
                self._update_screen(
                    station_id,
                    f"Tag not yet detected - hold bottle still ({elapsed_b:.0f}s)"
                )
            time.sleep(0.3)
        else:
            remaining = deadline - time.time()
            self.logger.warning(
                f"[{station_id}] No tag scan within {self.tag_wait_seconds}s of "
                "bottle placement. Is the sticker on the bottle bottom and the "
                "bottle fully seated on the reader?"
            )
            print(f"\n  [REG] No tag scan received in {self.tag_wait_seconds}s.")
            print("  Possible causes:")
            print("  - RFID sticker is not on the bottle bottom")
            print("  - Bottle not fully seated over the RC522 reader")
            print("  - RC522 firmware not running / not publishing to MQTT")
            self._update_screen(
                station_id,
                "No tag - check sticker on bottle bottom, re-seat bottle"
            )
            if remaining < 5:
                self._timeout(station_id)
                return False
            # Give user a chance to re-seat without full restart
            print(f"  Retrying... ({remaining:.0f}s remaining)")
            # Reset bottle detection so Phase A runs again
            return self._register_station_retry(station_id, deadline)

        # ---- Phase C: build record, persist, capture baseline ----
        record = self._build_registration_record(station_id, stable_weight, scan_msg)

        if record is None:
            self.logger.warning(
                f"[{station_id}] Tag payload unreadable. "
                "Ensure sticker was written with medication_tag_write_read_test."
            )
            print("  [REG] Tag payload could not be parsed.")
            print("  Check the sticker was written with medication_tag_write_read_test.ino")
            self.tag_runtime_service.clear_latest_scan()
            self._update_screen(station_id, "Tag unreadable - check sticker content")
            if self.audio:
                self.audio.speak("Tag unreadable. Please try again.")
            time.sleep(2.0)
            return self._register_station_retry(station_id, deadline)

        ok = self.database.upsert_registered_medicine(record)
        if not ok:
            self.logger.error(f"[{station_id}] Database write failed")
            return False
            
        # Capture baseline - use the stable weight reading from Phase A
        self.weight_manager.baseline_weights[station_id] = stable_weight
        self.weight_manager.baseline_capture_required[station_id] = False
        self.weight_manager._save_persisted_baselines()

        medicine_name = record.get("medicine_name", "Unknown")
        schedule_times = self._parse_schedule_times(record.get("time_slots", ""))

        self.logger.info(
            f"[{station_id}] Registered: {medicine_name}  "
            f"baseline={stable_weight:.2f}g  schedule={schedule_times}"
        )
        print(f"  [REG] SUCCESS: {medicine_name} registered on {station_id}")
        print(f"  [REG] Baseline: {stable_weight:.2f}g  Schedule: {schedule_times}")

        if self.display:
            self.display.show_registration_success_screen(medicine_name, schedule_times)
        if self.audio:
            self.audio.speak(f"{medicine_name} registered successfully.")

        self.telegram.send_registration_confirmation(
            medicine_name=medicine_name,
            station_id=station_id,
            dosage=record.get("dosage_amount", 0),
            schedule_times=schedule_times
        )

        t_end = time.time() + 4.0
        while time.time() < t_end:
            if self.display:
                self.display.update()
            time.sleep(0.05)

        return True

    def _register_station_retry(self, station_id: str, deadline: float) -> bool:
        """Re-enter the registration flow within the remaining deadline window."""
        remaining = deadline - time.time()
        if remaining < 5:
            self._timeout(station_id)
            return False
        print(f"  [REG] Retrying registration ({remaining:.0f}s remaining)...")
        self.timeout_seconds = remaining
        return self._register_station(station_id)

    def _timeout(self, station_id: str):
        self.logger.error(
            f"Registration timed out for {station_id} after {self.timeout_seconds}s"
        )
        print(f"  [REG] TIMEOUT: registration failed for {station_id}")
        if self.display:
            self.display.show_error_screen(
                f"Registration timed out for {station_id}.\n"
                "Restart the system and try again."
            )

    def _update_screen(self, station_id: str, message: str):
        if self.display:
            self.display.show_registration_screen(station_id, message)
            
    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_registration_record(
        self,
        station_id: str,
        weight_g: float,
        scan_msg: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Parse tag payload and build database-ready record."""
        from raspberry_pi.modules.tag_manager import TagManager
        tag_manager = TagManager(self.logger)
        record = tag_manager.build_record_from_scan(scan_msg)
        if not record:
            return None
        # Physical station always wins over tag payload S field
        record["station_id"] = station_id
        return record

    @staticmethod
    def _parse_schedule_times(time_slots: str) -> List[str]:
        """Convert 'HH:MM,HH:MM' string to ['HH:MM', 'HH:MM'] list."""
        if not time_slots:
            return []
        return [t.strip() for t in time_slots.split(",") if t.strip()]
