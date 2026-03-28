"""
Smart Medication System - Registration Manager

Handles the one-time medicine registration flow for each station.

Scan control during onboarding
--------------------------------
* start_scanning() is called at the TOP of each slot so the reader is
  active while the patient places the bottle and the tag settles.
* stop_scanning() is called as soon as a slot is SUCCESSFULLY registered
  and BEFORE the patient is asked to swap the bottle - preventing the
  reader from capturing the outgoing bottle's tag as a false "next" scan.
* stop_scanning() is also called on every failure / timeout exit path so
  the reader is never left running unexpectedly.
* run_onboarding_if_needed() calls stop_scanning() once after the final
  slot is confirmed, as the belt-and-suspenders final stop.

Tag payload format (written via medication_tag_writer_node.ino):
    ID=M001;N=ASPIRIN;D=2;T=08,20;M=AF;W=290

W field (optional): per-pill weight in milligrams.  When present it overrides
the hard-coded pill_weight_mg config value for this station, allowing the
system to use the correct weight for each specific medicine.

Note: P (patient_id) and S (station_id) are not written to the tag.
The station_id is always determined from the physical station (line 474),
never from the tag payload.
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
        self.enabled             = reg_cfg.get("enabled", True)
        self.timeout_seconds     = reg_cfg.get("timeout_seconds", 120)
        self.min_bottle_weight_g = reg_cfg.get("min_bottle_weight_g", 5.0)
        self.tag_wait_seconds    = reg_cfg.get("tag_wait_seconds", 30.0)

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

        Scanning is controlled per-slot inside _onboard_one_medicine.
        After the final slot succeeds this method sends one last
        stop_scan to guarantee the reader is idle.

        Returns True when the target count is reached.
        """
        if not self.enabled:
            self.logger.info("Registration disabled, skipping onboarding")
            return True

        # Count only medicines registered to THIS station so that a completed
        # station does not prevent a second station from being onboarded.
        all_registered   = self.database.list_registered_medicines()
        station_records  = [r for r in all_registered
                            if r.get("station_id") == station_id]
        registered_ids   = {r["medicine_id"] for r in station_records}
        registered_count = len(station_records)

        if registered_count >= expected_medicine_count:
            self.logger.info(
                f"All {expected_medicine_count} medicine(s) already registered "
                f"for {station_id}"
            )
            # Ensure scanner is off if we skip onboarding entirely
            self.tag_runtime_service.stop_scanning(station_id)
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
                # _onboard_one_medicine already called stop_scanning on failure
                return False

            # Refresh the set after each successful registration
            all_registered = self.database.list_registered_medicines()
            registered_ids = {r["medicine_id"] for r in all_registered
                              if r.get("station_id") == station_id}

        # All slots done - belt-and-suspenders final stop
        self.tag_runtime_service.stop_scanning(station_id)
        self.logger.info(
            f"Onboarding complete for {station_id}. Tag scanning stopped."
        )
        return True

    # ------------------------------------------------------------------
    # Single-slot onboarding
    # ------------------------------------------------------------------

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

        Scan control:
          START  - at the very top so the reader captures the tag the
                   moment the bottle touches the reader coil.
          STOP   - immediately after a successful registration and before
                   asking the patient to swap bottles, so the outgoing
                   bottle's tag is not captured as the next slot's scan.
          STOP   - on every early-exit (timeout, bad tag, etc.) so the
                   reader is never left running unintentionally.
        """
        self.logger.info(
            f"Onboarding slot {slot_number}/{total} on {station_id}"
        )

        # ----------------------------------------------------------------
        # START scanning for this slot
        # ----------------------------------------------------------------
        self.tag_runtime_service.start_scanning(station_id)
        self.logger.info(f"Tag scanning STARTED for onboarding slot {slot_number}")

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
        stable_weight        = None
        bottle_detected_at   = None

        while time.time() < deadline:
            if self.display:
                self.display.update()

            status   = self.weight_manager.get_station_status(station_id)
            if not status.get("connected"):
                time.sleep(0.5)
                continue

            weight_g  = float(status.get("weight_g") or 0.0)
            is_stable = status.get("stable", False)

            if weight_g < self.min_bottle_weight_g:
                bottle_detected_at = None
                time.sleep(0.3)
                continue

            if bottle_detected_at is None:
                bottle_detected_at = time.time()
                self._update_screen(
                    station_id,
                    "Bottle detected - stabilising..."
                )

            if not is_stable:
                time.sleep(0.3)
                continue

            stable_weight = weight_g
            # Show the confirmed stable weight now that the reading has settled
            self._update_screen(
                station_id,
                "Weight stable",
                weight_g=stable_weight,
                stable=True,
            )
            break
        else:
            self._timeout(station_id)
            self.tag_runtime_service.stop_scanning(station_id)   # stop on timeout
            return False

        # ---- Phase B: find tag scan ----
        # The tag is read the moment the bottle touches the reader coil,
        # which typically happens before or during weight stabilisation.
        # We accept any scan received since bottle_detected_at - 2 s.
        lookback_from = bottle_detected_at - 2.0
        self._update_screen(station_id, "Reading tag...",
                            weight_g=stable_weight, stable=True)
        if self.audio:
            self.audio.speak("Reading tag.")

        scan_msg    = None
        tag_deadline = time.time() + self.tag_wait_seconds

        while time.time() < tag_deadline and time.time() < deadline:
            if self.display:
                self.display.update()

            latest = self.tag_runtime_service.get_latest_scan(station_id)

            if not latest or latest.get("received_at", 0) < lookback_from:
                time.sleep(0.3)
                continue

            candidate_scan = latest["scan_msg"]

            # Validate the payload before accepting - an empty payload_raw
            # means the firmware read failed.  Discard it and keep waiting
            # for the firmware's retry scan.
            from raspberry_pi.modules.tag_manager import TagManager as _TM
            probe = _TM(self.logger).build_record_from_scan(candidate_scan)
            if probe is None:
                self.logger.warning(
                    "Tag scan received but payload could not be parsed "
                    f"(payload_raw={candidate_scan.get('payload_raw', '')!r}). "
                    "Waiting for valid retry scan..."
                )
                self._update_screen(
                    station_id,
                    "Tag detected but unreadable - keep bottle still...",
                    weight_g=stable_weight, stable=True,
                )
                self.tag_runtime_service.clear_latest_scan(station_id)
                time.sleep(0.5)
                continue

            # Good scan with parseable payload
            scan_msg = candidate_scan
            break
        else:
            self.logger.warning("No tag scan received during onboarding window")
            self._update_screen(station_id, "No tag - check sticker, re-seat bottle")
            if self.audio:
                self.audio.speak(
                    "No tag detected. Please check the sticker and try again."
                )
            self.tag_runtime_service.stop_scanning(station_id)   # stop on no-tag failure
            time.sleep(2.0)
            return False

        # ---- Phase C: build and validate record ----
        record = self._build_registration_record(station_id, stable_weight, scan_msg)
        if record is None:
            self._update_screen(station_id, "Tag unreadable - check sticker content")
            if self.audio:
                self.audio.speak("Tag could not be read. Please check the sticker.")
            self.tag_runtime_service.stop_scanning(station_id)   # stop on bad record
            time.sleep(2.0)
            return False

        medicine_id   = record.get("medicine_id")
        medicine_name = record.get("medicine_name", "Unknown")

        # ---- Duplicate check ----
        if medicine_id in registered_ids:
            self.logger.warning(
                f"Medicine {medicine_id} already registered - "
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
            # Stop scanning while the patient swaps bottles, then retry
            self.tag_runtime_service.stop_scanning(station_id)
            time.sleep(3.0)
            return self._onboard_one_medicine(
                station_id, slot_number, total, registered_ids, scheduler
            )

        # ---- Save to database ----
        ok = self.database.upsert_registered_medicine(record)
        if not ok:
            self.logger.error(f"Database write failed for {medicine_id}")
            self.tag_runtime_service.stop_scanning(station_id)   # stop on DB failure
            return False

        # ---- Apply tag-derived pill weight override ----
        pill_weight_mg = record.get("pill_weight_mg")
        if pill_weight_mg is not None:
            self.weight_manager.set_pill_weight_from_tag(station_id, pill_weight_mg)
        else:
            self.logger.info(
                f"Tag for {medicine_id} has no W field; "
                "pill weight falls back to config value"
            )

        # ---- Capture baseline ----
        self.weight_manager.baseline_weights[station_id]          = stable_weight
        self.weight_manager.baseline_capture_required[station_id] = False
        self.weight_manager._save_persisted_baselines()

        # ---- Parse schedule from tag payload ----
        schedule_times = self._parse_schedule_times(record.get("time_slots", ""))
        dosage         = record.get("dosage_amount", 0)

        # ---- Add to live scheduler ----
        if scheduler and schedule_times:
            scheduler.add_medication(
                medicine_name=medicine_name,
                station_id=station_id,
                dosage_pills=dosage,
                times=schedule_times
            )
            self.logger.info(
                f"Added {medicine_name} to live scheduler: {schedule_times}"
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

        # ---- Prompt for next bottle (if more slots remain) ----
        if slot_number < total:
            # STOP scanning BEFORE asking the patient to remove the current
            # bottle so we do not capture its tag as the next slot's scan.
            self.tag_runtime_service.stop_scanning(station_id)
            self.logger.info(
                f"Tag scanning STOPPED after slot {slot_number} "
                f"- waiting for bottle swap"
            )

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
                status   = self.weight_manager.get_station_status(station_id)
                weight_g = float(status.get("weight_g") or 0.0)
                if weight_g < self.min_bottle_weight_g:
                    break
                time.sleep(0.3)
            else:
                self.logger.warning(
                    "Bottle not removed after success - continuing anyway"
                )

            # Clear stale scan buffer before the next slot's start_scanning()
            self.tag_runtime_service.clear_latest_scan(station_id)
            time.sleep(1.0)

        # For the FINAL slot, stop_scanning() is called by
        # run_onboarding_if_needed() after the loop completes.

        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _timeout(self, station_id: str):
        self.logger.error(
            f"Registration timed out for {station_id} after {self.timeout_seconds}s"
        )
        if self.display:
            self.display.show_error_screen(
                f"Registration timed out for {station_id}.\n"
                "Restart the system and try again."
            )

    def _update_screen(self, station_id: str, message: str,
                       weight_g: float = None, stable: bool = False):
        if self.display:
            self.display.show_registration_screen(
                station_id, message, weight_g=weight_g, stable=stable
            )

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
