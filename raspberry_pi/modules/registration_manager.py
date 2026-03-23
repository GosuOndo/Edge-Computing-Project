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

    def run_onboarding_if_needed(
        self,
        station_id: str,
        expected_medicine_count: int,
        scheduler=None
    ) -> bool:
        """
        Sequential onboarding: register up to expected_medicine_count
        medicines on a single station, one at a time.

        After each successful registration, if a scheduler is provided,
        the medicine is dynamically added to the live schedule.

        Returns True when the target count is reached.
        """
        if not self.enabled:
            self.logger.info("Registration disabled, skipping onboarding")
            return True

        registered = self.database.list_registered_medicines()
        registered_ids = {r["medicine_id"] for r in registered}
        registered_count = len(registered)

        if registered_count >= expected_medicine_count:
            self.logger.info(
                f"All {expected_medicine_count} medicines already registered"
            )
            return True

        remaining = expected_medicine_count - registered_count
        self.logger.info(
            f"Onboarding: {registered_count}/{expected_medicine_count} registered, "
            f"need {remaining} more"
        )

        for slot in range(registered_count + 1, expected_medicine_count + 1):
            success = self._onboard_one_medicine(
                station_id=station_id,
                slot_number=slot,
                total=expected_medicine_count,
                registered_ids=registered_ids,
                scheduler=scheduler
            )
            if not success:
                self.logger.error(f"Onboarding failed at slot {slot}")
                return False
            # Refresh the set after each successful registration
            registered = self.database.list_registered_medicines()
            registered_ids = {r["medicine_id"] for r in registered}

        return True

    def _onboard_one_medicine(
        self,
        station_id: str,
        slot_number: int,
        total: int,
        registered_ids: set,
        scheduler=None
    ) -> bool:
        """
        Register a single medicine interactively.
        Waits for the user to place a bottle, reads the tag,
        confirms it is a new medicine, saves it, and adds it
        to the scheduler.
        """
        self.logger.info(
            f"Onboarding slot {slot_number}/{total} on {station_id}"
        )

        # ---- Guide the user ----
        msg = f"Medicine {slot_number} of {total} - Place bottle on station"
        if self.display:
            self.display.show_registration_screen(station_id, msg)
        if self.audio:
            self.audio.speak(
                f"Please place medicine {slot_number} of {total} "
                f"onto the station now."
            )

        deadline = time.time() + self.timeout_seconds

        # ---- Phase A: wait for stable weight ----
        stable_weight = None
        bottle_detected_at = None

        while time.time() < deadline:
            if self.display:
                self.display.update()

            status = self.weight_manager.get_station_status(station_id)
            if not status.get("connected"):
                time.sleep(0.5)
                continue

            weight_g = float(status.get("weight_g") or 0.0)
            is_stable = status.get("stable", False)

            if weight_g < self.min_bottle_weight_g:
                bottle_detected_at = None
                time.sleep(0.3)
                continue

            if bottle_detected_at is None:
                bottle_detected_at = time.time()
                self._update_screen(
                    station_id,
                    f"Bottle detected ({weight_g:.1f}g) - stabilising..."
                )

            if not is_stable:
                time.sleep(0.3)
                continue

            stable_weight = weight_g
            break
        else:
            self._timeout(station_id)
            return False

        # ---- Phase B: find tag scan ----
        lookback_from = bottle_detected_at - 2.0
        self._update_screen(station_id, "Reading tag...")
        if self.audio:
            self.audio.speak("Reading tag.")
        scan_msg = None
        tag_deadline = time.time() + self.tag_wait_seconds

        while time.time() < tag_deadline and time.time() < deadline:
            if self.display:
                self.display.update()
            latest = self.tag_runtime_service.get_latest_scan()
            if latest and latest.get("received_at", 0) >= lookback_from:
                scan_msg = latest["scan_msg"]
                break
            time.sleep(0.3)
        else:
            self.logger.warning("No tag scan received during onboarding window")
            self._update_screen(station_id, "No tag - check sticker, re-seat bottle")
            if self.audio:
                self.audio.speak(
                    "No tag detected. Please check the sticker and try again."
                )
            time.sleep(2.0)
            return False

        # ---- Phase C: build and validate record ----
        record = self._build_registration_record(station_id, stable_weight, scan_msg)
        if record is None:
            self._update_screen(station_id, "Tag unreadable - check sticker content")
            if self.audio:
                self.audio.speak("Tag could not be read. Please check the sticker.")
            time.sleep(2.0)
            return False

        medicine_id = record.get("medicine_id")
        medicine_name = record.get("medicine_name", "Unknown")

        # ---- Duplicate check ----
        if medicine_id in registered_ids:
            self.logger.warning(
                f"Medicine {medicine_id} already registered "
                f"asking user to place a different bottle"
            )
            self._update_screen(
                station_id,
                f"{medicine_name} already registered - place a different bottle"
            )
            if self.audio:
                self.audio.speak(
                    f"{medicine_name} is already registered. "
                    f"Please place a different medicine bottle."
                )
            time.sleep(3.0)
            # Retry this slot
            return self._onboard_one_medicine(
                station_id, slot_number, total, registered_ids, scheduler
            )

        # ---- Save to database ----
        ok = self.database.upsert_registered_medicine(record)
        if not ok:
            self.logger.error(f"Database write failed for {medicine_id}")
            return False

        # ---- Capture baseline ----
        self.weight_manager.baseline_weights[station_id] = stable_weight
        self.weight_manager.baseline_capture_required[station_id] = False
        self.weight_manager._save_persisted_baselines()

        # ---- Parse schedule from tag payload ----
        schedule_times = self._parse_schedule_times(record.get("time_slots", ""))
        dosage = record.get("dosage_amount", 0)

        # ---- Add to live scheduler ----
        if scheduler and schedule_times:
            scheduler.add_medication(
                medicine_name=medicine_name,
                station_id=station_id,
                dosage_pills=dosage,
                times=schedule_times
            )
            self.logger.info(
                f"Added {medicine_name} to live scheduler: "
                f"{schedule_times}"
            )

        # ---- Notify user ----
        self.logger.info(
            f"Registered: {medicine_name} ({medicine_id}) "
            f"baseline={stable_weight:.2f}g schedule={schedule_times}"
        )

        if self.display:
            self.display.show_registration_success_screen(medicine_name, schedule_times)

        if self.audio:
            times_spoken = " and ".join(schedule_times) if schedule_times else "as scheduled"
            self.audio.speak(
                f"{medicine_name} registered successfully. "
                f"You will be reminded at {times_spoken}."
            )

        # ---- Send Telegram confirmation ----
        self.telegram.send_registration_confirmation(
            medicine_name=medicine_name,
            station_id=station_id,
            dosage=dosage,
            schedule_times=schedule_times
        )
    
        # ---- Hold success screen briefly ----
        hold_until = time.time() + 4.0
        while time.time() < hold_until:
            if self.display:
                self.display.update()
            time.sleep(0.05)

        # ---- Prompt for next bottle ----
        if slot_number < total:
            next_msg = (
                f"Medicine {slot_number} done. "
                f"Remove bottle and place medicine {slot_number + 1}."
            )
            self._update_screen(station_id, next_msg)
            if self.audio:
                self.audio.speak(
                    f"Medicine {slot_number} registered. "
                    f"Please remove the bottle and place the next medicine."
                )

            # Wait for bottle to be removed before returning
            remove_deadline = time.time() + 30.0
            while time.time() < remove_deadline:
                if self.display:
                    self.display.update()
                status = self.weight_manager.get_station_status(station_id)
                weight_g = float(status.get("weight_g") or 0.0)
                if weight_g < self.min_bottle_weight_g:
                    break
                time.sleep(0.3)
            else:
                self.logger.warning("Bottle not removed after success - continuing anyway")

            # Clear the tag scan buffer so the next bottle gets a fresh read
            self.tag_runtime_service.clear_latest_scan()
            time.sleep(1.0)

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
