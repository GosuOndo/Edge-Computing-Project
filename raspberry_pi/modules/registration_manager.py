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
The station_id is always determined from the physical station,
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
        scheduler=None,
        station_number: int = 1,
        station_total: int = 1,
    ) -> bool:
        """
        Sequential onboarding: register up to expected_medicine_count
        medicines on a single station, one at a time.

        Args:
            station_id:              The station to onboard.
            expected_medicine_count: How many medicines this station holds (usually 1).
            scheduler:               Live scheduler to add medicines to.
            station_number:          1-based index of this station in the overall
                                     onboarding sequence (e.g. 1 of 2).  Used in
                                     display/audio so the patient sees the correct
                                     "Station X of Y" progress instead of the
                                     always-misleading "Medicine 1 of 1".
            station_total:           Total number of stations being onboarded.

        Returns True when the target count is reached.
        """
        if not self.enabled:
            self.logger.info("Registration disabled, skipping onboarding")
            return True

        # Count only medicines registered to THIS station (for progress tracking).
        all_registered   = self.database.list_registered_medicines()
        station_records  = [r for r in all_registered
                            if r.get("station_id") == station_id]
        registered_count = len(station_records)

        # Duplicate detection is global: the same medicine must not appear on
        # any station, not just the current one.
        registered_ids = {r["medicine_id"] for r in all_registered}

        if registered_count >= expected_medicine_count:
            self.logger.info(
                f"All {expected_medicine_count} medicine(s) already registered "
                f"for {station_id}"
            )
            self.tag_runtime_service.stop_scanning(station_id)
            return True

        remaining = expected_medicine_count - registered_count
        self.logger.info(
            f"Onboarding: {registered_count}/{expected_medicine_count} registered "
            f"for {station_id}, need {remaining} more"
        )

        for slot in range(registered_count + 1, expected_medicine_count + 1):
            success = self._onboard_one_medicine(
                station_id=station_id,
                slot_number=slot,
                total=expected_medicine_count,
                registered_ids=registered_ids,
                scheduler=scheduler,
                station_number=station_number,
                station_total=station_total,
            )
            if not success:
                self.logger.error(f"Onboarding failed at slot {slot} for {station_id}")
                return False

            # Refresh global registered_ids after each successful slot so the
            # next slot cannot re-use a medicine already on any station.
            all_registered = self.database.list_registered_medicines()
            registered_ids = {r["medicine_id"] for r in all_registered}

        # Belt-and-suspenders: ensure scanner is stopped after all slots.
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
        scheduler=None,
        station_number: int = 1,
        station_total: int = 1,
    ) -> bool:
        """
        Register a single medicine interactively.

        Uses a retry loop instead of recursion so station_number (and
        slot_number) are always accurate in display/audio output.
        """
        while True:
            result = self._attempt_one_slot(
                station_id=station_id,
                slot_number=slot_number,
                total=total,
                registered_ids=registered_ids,
                scheduler=scheduler,
                station_number=station_number,
                station_total=station_total,
            )
            if result == "success":
                return True
            elif result == "duplicate_retry":
                continue
            else:
                return False

    def _attempt_one_slot(
        self,
        station_id: str,
        slot_number: int,
        total: int,
        registered_ids: set,
        scheduler=None,
        station_number: int = 1,
        station_total: int = 1,
    ) -> str:
        """
        One full attempt to register a single medicine slot.

        Display/audio uses station_number/station_total (e.g. "Station 2 of 3")
        when there is only one medicine per station (the common case), so the
        patient always sees meaningful progress.  When a station genuinely holds
        multiple medicines the inner slot_number/total is appended as well.

        Returns:
            "success"         - medicine registered successfully
            "duplicate_retry" - duplicate detected; caller retries same slot
            "failure"         - unrecoverable error; abort onboarding
        """
        # Human-readable progress label shown to the patient.
        # One medicine per station  -> "Station 2 of 3"
        # Multiple medicines/station -> "Station 2 of 3 · Medicine 1 of 2"
        if total <= 1:
            progress_label = f"Station {station_number} of {station_total}"
        else:
            progress_label = (
                f"Station {station_number} of {station_total}"
                f" \u00b7 Medicine {slot_number} of {total}"
            )

        self.logger.info(f"Onboarding {progress_label} ({station_id})")

        # ---- START scanning ----
        self.tag_runtime_service.start_scanning(station_id)
        self.logger.info(f"Tag scanning STARTED for {progress_label}")

        # ---- Guide the user ----
        msg = f"{progress_label} - Place bottle on station"
        if self.display:
            self.display.show_registration_screen(station_id, msg)
        if self.audio:
            self.audio.speak(
                f"Please place the medicine for {progress_label} onto the station now."
            )

        deadline = time.time() + self.timeout_seconds

        # ---- Phase A: wait for stable weight ----
        stable_weight      = None
        bottle_detected_at = None

        while time.time() < deadline:
            if self.display:
                self.display.update()

            status = self.weight_manager.get_station_status(station_id)
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
                self._update_screen(station_id, "Bottle detected - stabilising...")

            if not is_stable:
                time.sleep(0.3)
                continue

            stable_weight = weight_g
            self._update_screen(
                station_id,
                "Weight stable",
                weight_g=stable_weight,
                stable=True,
            )
            break
        else:
            self._timeout(station_id)
            self.tag_runtime_service.stop_scanning(station_id)
            return "failure"

        # ---- Phase B: find tag scan ----
        lookback_from = bottle_detected_at - 2.0
        self._update_screen(station_id, "Reading tag...",
                            weight_g=stable_weight, stable=True)
        if self.audio:
            self.audio.speak("Reading tag.")

        scan_msg     = None
        tag_deadline = time.time() + self.tag_wait_seconds

        while time.time() < tag_deadline and time.time() < deadline:
            if self.display:
                self.display.update()

            latest = self.tag_runtime_service.get_latest_scan(station_id)
            if not latest or latest.get("received_at", 0) < lookback_from:
                time.sleep(0.3)
                continue

            candidate_scan = latest["scan_msg"]

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

            scan_msg = candidate_scan
            break
        else:
            self.logger.warning("No tag scan received during onboarding window")
            self._update_screen(station_id, "No tag - check sticker, re-seat bottle")
            if self.audio:
                self.audio.speak(
                    "No tag detected. Please check the sticker and try again."
                )
            self.tag_runtime_service.stop_scanning(station_id)
            time.sleep(2.0)
            return "failure"

        # ---- Phase C: build and validate record ----
        record = self._build_registration_record(station_id, stable_weight, scan_msg)
        if record is None:
            self._update_screen(station_id, "Tag unreadable - check sticker content")
            if self.audio:
                self.audio.speak("Tag could not be read. Please check the sticker.")
            self.tag_runtime_service.stop_scanning(station_id)
            time.sleep(2.0)
            return "failure"

        medicine_id   = record.get("medicine_id")
        medicine_name = record.get("medicine_name", "Unknown")

        # ---- Duplicate check (global across all stations) ----
        if medicine_id in registered_ids:
            self.logger.warning(
                f"Medicine {medicine_id} already registered on the system - "
                "asking user to place a different bottle"
            )
            self._update_screen(
                station_id,
                f"{medicine_name} already registered - place a different bottle"
            )
            if self.audio:
                self.audio.speak(
                    f"{medicine_name} is already registered. "
                    "Please place a different medicine bottle."
                )
            self.tag_runtime_service.stop_scanning(station_id)
            time.sleep(3.0)
            self.tag_runtime_service.clear_latest_scan(station_id)
            return "duplicate_retry"

        # ---- Save to database ----
        ok = self.database.upsert_registered_medicine(record)
        if not ok:
            self.logger.error(f"Database write failed for {medicine_id}")
            self.tag_runtime_service.stop_scanning(station_id)
            return "failure"

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
            times_spoken = (
                " and ".join(schedule_times) if schedule_times else "as scheduled"
            )
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

        # ---- Prompt for next bottle/station if more remain ----
        more_slots    = slot_number < total
        more_stations = station_number < station_total

        if more_slots or more_stations:
            # STOP scanning BEFORE asking the patient to swap so we do not
            # capture the outgoing bottle's tag as the next slot's scan.
            self.tag_runtime_service.stop_scanning(station_id)
            self.logger.info(
                f"Tag scanning STOPPED after {progress_label} - waiting for swap"
            )

            if more_slots:
                # Multiple medicines on same station.
                if total <= 1:
                    next_label = f"Station {station_number + 1} of {station_total}"
                else:
                    next_label = (
                        f"Station {station_number} of {station_total}"
                        f" \u00b7 Medicine {slot_number + 1} of {total}"
                    )
                next_msg = (
                    f"{progress_label} done. "
                    f"Remove bottle and place medicine {slot_number + 1} of {total}."
                )
                spoken = (
                    f"{progress_label} registered. "
                    f"Please remove the bottle and place medicine {slot_number + 1}."
                )
            else:
                # One medicine per station - move to next station.
                next_label = f"Station {station_number + 1} of {station_total}"
                next_msg = (
                    f"{progress_label} done. "
                    f"Remove bottle and move to {next_label}."
                )
                spoken = (
                    f"{progress_label} registered. "
                    f"Please remove the bottle and move to {next_label}."
                )

            self._update_screen(station_id, next_msg)
            if self.audio:
                self.audio.speak(spoken)

            # Wait for bottle to be removed before returning.
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

            # Clear stale scan buffer before the next slot's start_scanning().
            self.tag_runtime_service.clear_latest_scan(station_id)
            time.sleep(1.0)

        # For the final slot on the final station, stop_scanning() is called
        # by run_onboarding_if_needed() after the loop completes.

        return "success"

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
        # Physical station always wins over tag payload S field.
        record["station_id"] = station_id
        return record

    @staticmethod
    def _parse_schedule_times(time_slots: str) -> List[str]:
        """Convert 'HH:MM,HH:MM' string to ['HH:MM', 'HH:MM'] list."""
        if not time_slots:
            return []
        return [t.strip() for t in time_slots.split(",") if t.strip()]