"""
Smart Medication System - Display Manager Module

Unified design system
---------------------
All screens share the same structural template:

  y=0        8px accent bar  (color varies per screen type)
  y=42       Screen title    (centered, 'large' font, text_dark)
  y=72       Divider line
  y=85–540   White card panel with a 12px coloured top strip
  y=540–600  Footer bar      (60px, primary colour or contextual)

Screen-type colours:
  idle / registration / pipeline  → primary   (blue)
  reminder / watching              → warning   (orange)
  monitoring                       → secondary (teal)
  success / reg-success            → success   (green)
  warning / error                  → error     (red)
"""

import pygame
from datetime import datetime
from threading import Lock


class DisplayManager:

    # ------------------------------------------------------------------
    # Layout constants
    # ------------------------------------------------------------------
    _ACCENT_H   = 8      # top accent bar height
    _TITLE_Y    = 42     # screen title vertical centre
    _DIVIDER_Y  = 72     # horizontal rule y
    _PANEL_X    = 70     # card left edge
    _PANEL_W    = 884    # card width  (1024 - 2×70)
    _PANEL_Y    = 85     # card top edge
    _PANEL_BOT  = 540    # card bottom edge
    _STRIP_H    = 12     # coloured strip at top of card
    _FOOTER_Y   = 540    # footer top edge
    _FOOTER_H   = 60     # footer height

    def __init__(self, config: dict, logger):
        self.config    = config
        self.logger    = logger
        self.width     = config.get('width',      1024)
        self.height    = config.get('height',     600)
        self.fullscreen = config.get('fullscreen', True)

        self.colors = {
            'background': (240, 240, 245),
            'primary':    (44,  95,  126),
            'secondary':  (74,  144, 164),
            'success':    (82,  183, 136),
            'warning':    (244, 162, 97),
            'error':      (232, 93,  117),
            'text_dark':  (43,  45,  66),
            'text_light': (141, 153, 174),
            'white':      (255, 255, 255),
            'black':      (0,   0,   0),
        }

        self.screen      = None
        self.clock       = None
        self.initialized = False
        self.screen_lock = Lock()
        self.fonts       = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        try:
            pygame.init()
            flags = pygame.FULLSCREEN if self.fullscreen else 0
            self.screen = pygame.display.set_mode(
                (self.width, self.height), flags
            )
            pygame.display.set_caption("Smart Medication System")
            self.clock = pygame.time.Clock()

            self.fonts = {
                'huge':   pygame.font.Font(None, 96),
                'large':  pygame.font.Font(None, 56),
                'medium': pygame.font.Font(None, 38),
                'normal': pygame.font.Font(None, 28),
                'small':  pygame.font.Font(None, 22),
            }

            self.initialized = True
            self.logger.info("Display initialized")
            return True

        except Exception as e:
            self.logger.error(f"Display init failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Low-level drawing helpers
    # ------------------------------------------------------------------

    def _draw_text(self, text, font_key, color_key, x, y,
                   center=False, right=False):
        """
        Render text onto the screen.

        Alignment (pick at most one):
          center=True  → x,y is the centre of the text box
          right=True   → x is the right edge; y is the vertical centre
          (neither)    → x,y is the top-left corner (default)
        """
        font    = self.fonts[font_key]
        color   = self.colors[color_key]
        surface = font.render(str(text), True, color)
        if center:
            rect = surface.get_rect(center=(x, y))
        elif right:
            rect = surface.get_rect(midright=(x, y))
        else:
            rect = surface.get_rect(topleft=(x, y))
        self.screen.blit(surface, rect)

    def _draw_rect(self, color_key, x, y, w, h, radius=0):
        pygame.draw.rect(
            self.screen, self.colors[color_key],
            (x, y, w, h), border_radius=radius
        )

    def _fill(self, color_key):
        self.screen.fill(self.colors[color_key])

    def _wrap_text(self, text, font_key='normal', max_width=760):
        font  = self.fonts[font_key]
        words = str(text).split()
        lines, current = [], ""
        for word in words:
            test = word if not current else f"{current} {word}"
            if font.size(test)[0] <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    # ------------------------------------------------------------------
    # Structural helpers (shared by all screens)
    # ------------------------------------------------------------------

    def _draw_frame(self, title: str, accent_color: str):
        """
        Draw the common structural frame shared by all screens:
          - background fill
          - top accent bar
          - centred title
          - horizontal divider
        """
        self._fill('background')
        self._draw_rect(accent_color, 0, 0, self.width, self._ACCENT_H)
        self._draw_text(title, 'large', 'text_dark',
                        self.width // 2, self._TITLE_Y, center=True)
        pygame.draw.line(
            self.screen, self.colors['text_light'],
            (self._PANEL_X, self._DIVIDER_Y),
            (self._PANEL_X + self._PANEL_W, self._DIVIDER_Y), 1
        )

    def _draw_card(self, strip_color: str):
        """Draw the white card panel with a coloured top strip."""
        h = self._PANEL_BOT - self._PANEL_Y
        self._draw_rect('white', self._PANEL_X, self._PANEL_Y,
                        self._PANEL_W, h, radius=16)
        self._draw_rect(strip_color, self._PANEL_X, self._PANEL_Y,
                        self._PANEL_W, self._STRIP_H, radius=16)

    def _draw_footer(self, text: str,
                     bg_color: str = 'primary',
                     text_color: str = 'white'):
        """Draw the footer bar with centred instruction text."""
        self._draw_rect(bg_color, 0, self._FOOTER_Y,
                        self.width, self._FOOTER_H)
        self._draw_text(text, 'normal', text_color,
                        self.width // 2,
                        self._FOOTER_Y + self._FOOTER_H // 2,
                        center=True)

    def _card_text_y(self, offset: int = 0) -> int:
        """
        Starting y for the first line of text inside the card,
        after the strip and a standard top-padding.
        """
        return self._PANEL_Y + self._STRIP_H + 28 + offset

    # ------------------------------------------------------------------
    # Idle screen
    # ------------------------------------------------------------------

    def _normalize_idle_screen_data(self, idle_data):
        next_medication = None
        today_schedule  = []
        if isinstance(idle_data, dict):
            if "next_medication" in idle_data or "today_schedule" in idle_data:
                next_medication = idle_data.get("next_medication")
                today_schedule  = idle_data.get("today_schedule") or []
            elif (
                "medicine_name" in idle_data
                or "time" in idle_data
                or "time_until" in idle_data
            ):
                next_medication = idle_data
        elif isinstance(idle_data, str):
            next_medication = {"medicine_name": idle_data}
        return next_medication, today_schedule

    def show_idle_screen(self, next_medication=None):
        if not self.initialized:
            return
        with self.screen_lock:
            next_medication, today_schedule = self._normalize_idle_screen_data(
                next_medication
            )
            time_str = datetime.now().strftime('%H:%M')
            date_str = datetime.now().strftime('%A, %d %B %Y')

            # Draw accent bar and divider only; no title text so the header
            # band is free for the clock / system-name layout.
            self._draw_frame('', 'primary')

            right_edge = self._PANEL_X + self._PANEL_W  # x = 954

            # System name – small, left-aligned, vertically centred in header
            self._draw_text(
                "Smart Medication System", 'small', 'text_light',
                self._PANEL_X, self._TITLE_Y, center=False
                # topleft; small font ~16 px tall, TITLE_Y=42 → visible ≈ 42–58
            )

            # Clock – large, right-aligned, vertically centred in header
            self._draw_text(
                time_str, 'large', 'primary',
                right_edge, self._TITLE_Y + 6, right=True
                # midright; large font ~40 px tall → visible ≈ 28–68
            )

            # Date – small, right-aligned just above the divider line
            self._draw_text(
                date_str, 'small', 'text_light',
                right_edge, self._DIVIDER_Y - 4, right=True
            )

            # ---- Next Medication card (top half) ----
            nm_x, nm_y, nm_w, nm_h = self._PANEL_X, self._PANEL_Y, self._PANEL_W, 110
            self._draw_rect('white', nm_x, nm_y, nm_w, nm_h, radius=16)
            self._draw_rect('secondary', nm_x, nm_y, nm_w, self._STRIP_H, radius=16)
            self._draw_text("Next Medication", 'small', 'text_light',
                            nm_x + 28, nm_y + 22)

            if next_medication:
                name      = next_medication.get('medicine_name', 'Unknown')
                time_val  = next_medication.get('time', '')
                station_val = next_medication.get('station_id', '')
                summary   = f"at {time_val}" if time_val else "Scheduled"
                if station_val:
                    summary = f"{summary}  |  {station_val.replace('_', ' ').title()}"
                self._draw_text(name, 'medium', 'primary',
                                nm_x + 28, nm_y + 52)
                self._draw_text(summary, 'normal', 'secondary',
                                nm_x + 28, nm_y + 84)
            else:
                self._draw_text("No medications scheduled", 'medium', 'text_light',
                                nm_x + 28, nm_y + 58)

            # ---- Daily Schedule card (bottom half) ----
            sched_x = self._PANEL_X
            sched_y = self._PANEL_Y + nm_h + 12
            sched_w = self._PANEL_W
            sched_h = self._PANEL_BOT - sched_y

            self._draw_rect('white', sched_x, sched_y, sched_w, sched_h, radius=16)
            self._draw_rect('primary', sched_x, sched_y, sched_w, self._STRIP_H, radius=16)
            self._draw_text("Daily Schedule", 'normal', 'primary',
                            sched_x + 28, sched_y + 22)

            if today_schedule:
                row_y  = sched_y + 58
                row_h  = 36
                max_rows = 5
                for idx, item in enumerate(today_schedule[:max_rows]):
                    if idx > 0:
                        pygame.draw.line(
                            self.screen, self.colors['background'],
                            (sched_x + 24, row_y - 14),
                            (sched_x + sched_w - 24, row_y - 14), 1
                        )
                    if isinstance(item, str):
                        self._draw_text(item, 'normal', 'text_dark',
                                        sched_x + 28, row_y)
                    else:
                        station_label = str(
                            item.get('station_id', '')
                        ).replace('_', ' ').title()
                        details = item.get('medicine_name', 'Unknown')
                        if station_label:
                            details = f"{details}  ({station_label})"
                        self._draw_text(item.get('time', '--:--'), 'normal', 'secondary',
                                        sched_x + 28, row_y)
                        self._draw_text(details, 'normal', 'text_dark',
                                        sched_x + 150, row_y)
                    row_y += row_h
                if len(today_schedule) > max_rows:
                    self._draw_text(
                        f"+ {len(today_schedule) - max_rows} more scheduled dose(s)",
                        'small', 'text_light',
                        sched_x + 28, sched_y + sched_h - 26
                    )
            else:
                self._draw_text(
                    "No registered medicine timetable available yet",
                    'normal', 'text_light',
                    self.width // 2, sched_y + sched_h // 2, center=True
                )

            self._draw_footer("Smart Medication System", 'primary')
            pygame.display.flip()

    # ------------------------------------------------------------------
    # Reminder screen
    # ------------------------------------------------------------------

    def show_reminder_screen(self, medicine_name, dosage, time_str):
        if not self.initialized:
            return
        with self.screen_lock:
            self._draw_frame("TIME FOR MEDICATION", 'warning')
            self._draw_card('warning')

            cy = self._card_text_y(20)
            self._draw_text(medicine_name, 'huge', 'primary',
                            self.width // 2, cy, center=True)

            self._draw_text(f"Take  {dosage}  pill(s)", 'large', 'text_dark',
                            self.width // 2, cy + 110, center=True)

            self._draw_text(f"Scheduled at  {time_str}", 'medium', 'text_light',
                            self.width // 2, cy + 180, center=True)

            pygame.draw.line(
                self.screen, self.colors['text_light'],
                (self._PANEL_X + 40, cy + 230),
                (self._PANEL_X + self._PANEL_W - 40, cy + 230), 1
            )

            self._draw_text(
                "Lift the bottle, remove your pills, then place the bottle back on the scale",
                'small', 'text_light', self.width // 2, cy + 260, center=True
            )

            self._draw_footer(
                "Remove pills from bottle  |  Place bottle back on scale",
                'warning'
            )
            pygame.display.flip()

    # ------------------------------------------------------------------
    # Registration screen
    # ------------------------------------------------------------------

    def show_registration_screen(
        self,
        station_id: str,
        status_message: str = "Waiting for medicine...",
        weight_g: float = None,
        stable: bool = False,
    ):
        """
        Registration / onboarding status screen.

        Parameters
        ----------
        station_id     : e.g. "station_1"
        status_message : current onboarding step description
        weight_g       : current scale reading (grams). Pass only the
                         confirmed STABLE weight so that the display never
                         shows a fluctuating unstable value.
        stable         : must be True for weight_g to be displayed.
        """
        if not self.initialized:
            return
        with self.screen_lock:
            self._draw_frame("MEDICINE REGISTRATION", 'primary')
            self._draw_card('primary')

            station_label = station_id.replace('_', ' ').title()
            self._draw_text(station_label, 'normal', 'text_light',
                            self.width // 2, self._card_text_y(8), center=True)

            # Status message – wrapped and centered
            msg_y = self._card_text_y(52)
            for line in self._wrap_text(status_message, 'medium', max_width=780):
                self._draw_text(line, 'medium', 'primary',
                                self.width // 2, msg_y, center=True)
                msg_y += 46

            # Weight – only shown when the reading is confirmed stable
            if weight_g is not None and stable:
                weight_y = self._card_text_y(200)
                self._draw_text(f"{weight_g:.2f} g", 'large', 'success',
                                self.width // 2, weight_y, center=True)
                self._draw_text("STABLE", 'small', 'success',
                                self.width // 2, weight_y + 46, center=True)

            # Bottom hint
            hint_y = self._PANEL_BOT - 54
            self._draw_text(
                "Place bottle on scale  |  Ensure NFC tag faces the reader",
                'small', 'text_light', self.width // 2, hint_y, center=True
            )

            self._draw_footer("Smart Medication System  |  Registration", 'primary')
            pygame.display.flip()

    # ------------------------------------------------------------------
    # Registration success screen
    # ------------------------------------------------------------------

    def show_registration_success_screen(self, medicine_name, schedule_times):
        if not self.initialized:
            return
        with self.screen_lock:
            self._draw_frame("REGISTRATION COMPLETE", 'success')
            self._draw_card('success')

            cy = self._card_text_y(30)
            self._draw_text(medicine_name, 'huge', 'text_dark',
                            self.width // 2, cy, center=True)

            self._draw_text("Medicine registered successfully", 'medium', 'success',
                            self.width // 2, cy + 110, center=True)

            if schedule_times:
                times_str = "   ".join(schedule_times)
                self._draw_text(f"Scheduled at:  {times_str}", 'normal', 'text_light',
                                self.width // 2, cy + 170, center=True)

            self._draw_footer(
                "Your caregiver has been notified.", 'success'
            )
            pygame.display.flip()

    # ------------------------------------------------------------------
    # Pipeline / verification progress screen
    # ------------------------------------------------------------------

    def show_pipeline_screen(self, stage_name: str, detail: str = "Please wait..."):
        if not self.initialized:
            return
        with self.screen_lock:
            self._draw_frame("MEDICATION VERIFICATION", 'secondary')
            self._draw_card('secondary')

            cy = self._card_text_y(20)
            self._draw_text(stage_name, 'large', 'text_dark',
                            self.width // 2, cy, center=True)

            detail_y = cy + 80
            for line in self._wrap_text(detail, 'medium', max_width=760):
                self._draw_text(line, 'medium', 'text_dark',
                                self.width // 2, detail_y, center=True)
                detail_y += 46

            self._draw_text("Processing...", 'normal', 'secondary',
                            self.width // 2, self._PANEL_BOT - 60, center=True)

            self._draw_footer("Smart Medication System  |  Verification", 'secondary')
            pygame.display.flip()

    # ------------------------------------------------------------------
    # Live-weight watching screen
    # ------------------------------------------------------------------

    def show_watching_screen(self, title: str, weight_g: float, stable: bool,
                              baseline_g: float, phase: str, armed: bool,
                              elapsed: float = 0, timeout: float = 90):
        """
        Live weight display shown while waiting for pill removal.
        Updates every loop iteration.
        """
        if not self.initialized:
            return
        with self.screen_lock:
            self._draw_frame(title, 'warning' if not stable else 'secondary')
            self._draw_card('secondary')

            # Large weight readout
            weight_color = 'success' if stable else 'warning'
            self._draw_text(f"{weight_g:.2f} g", 'huge', weight_color,
                            self.width // 2, self._card_text_y(30), center=True)
            self._draw_text("STABLE" if stable else "SETTLING...",
                            'medium', weight_color,
                            self.width // 2, self._card_text_y(120), center=True)

            # Info row
            info_y = self._card_text_y(180)
            for label, value in [
                ("Baseline", f"{baseline_g:.2f} g"),
                ("Phase",    phase.replace('_', ' ')),
                ("Armed",    "YES" if armed else "NO"),
            ]:
                self._draw_text(f"{label}:  {value}", 'normal', 'text_light',
                                self.width // 2, info_y, center=True)
                info_y += 32

            # Progress bar
            bar_w = 640
            bar_x = (self.width - bar_w) // 2
            bar_y = self._PANEL_BOT - 80
            self._draw_rect('text_light', bar_x, bar_y, bar_w, 18, radius=9)
            progress = min(elapsed / max(timeout, 1), 1.0)
            fill_w = int(bar_w * progress)
            if fill_w > 0:
                pygame.draw.rect(
                    self.screen, self.colors['secondary'],
                    (bar_x, bar_y, fill_w, 18), border_radius=9
                )

            remaining = max(0, int(timeout - elapsed))
            self._draw_footer(
                f"Lift bottle  |  Remove pills  |  Place back  |  {remaining}s remaining",
                'secondary'
            )
            pygame.display.flip()

    # ------------------------------------------------------------------
    # Monitoring screen
    # ------------------------------------------------------------------

    def show_monitoring_screen(self, elapsed: float, duration: float,
                                message: str = "Monitoring intake..."):
        if not self.initialized:
            return
        with self.screen_lock:
            self._draw_frame("MONITORING INTAKE", 'secondary')
            self._draw_card('secondary')

            cy = self._card_text_y(10)
            self._draw_text(message, 'medium', 'text_dark',
                            self.width // 2, cy, center=True)

            self._draw_text(
                "Bring your hand to your mouth and open your mouth clearly",
                'normal', 'text_light', self.width // 2, cy + 52, center=True
            )

            # Progress bar
            bar_w = 700
            bar_x = (self.width - bar_w) // 2
            bar_y = cy + 120
            self._draw_rect('text_light', bar_x, bar_y, bar_w, 36, radius=18)
            progress = min(elapsed / duration, 1.0) if duration > 0 else 0
            fill_w   = int(bar_w * progress)
            if fill_w > 0:
                pygame.draw.rect(
                    self.screen, self.colors['secondary'],
                    (bar_x, bar_y, fill_w, 36), border_radius=18
                )
            self._draw_text(f"{int(progress * 100)}%", 'medium', 'text_dark',
                            self.width // 2, bar_y + 52, center=True)

            remaining = max(0, int(duration - elapsed))
            self._draw_text(f"{remaining} seconds remaining", 'medium', 'text_light',
                            self.width // 2, bar_y + 100, center=True)

            self._draw_footer(
                "Please remain in front of the camera", 'secondary'
            )
            pygame.display.flip()

    # ------------------------------------------------------------------
    # Success screen
    # ------------------------------------------------------------------

    def show_success_screen(
        self,
        medicine_name: str,
        message: str = "Medication taken successfully!",
    ):
        if not self.initialized:
            return
        with self.screen_lock:
            self._draw_frame("MEDICATION VERIFIED", 'success')
            self._draw_card('success')

            cy = self._card_text_y(20)
            self._draw_text(medicine_name, 'huge', 'text_dark',
                            self.width // 2, cy, center=True)

            self._draw_text(message, 'medium', 'success',
                            self.width // 2, cy + 110, center=True)

            time_str = datetime.now().strftime('%H:%M:%S')
            self._draw_text(f"Confirmed at  {time_str}", 'normal', 'text_light',
                            self.width // 2, cy + 168, center=True)

            self._draw_footer(
                "Well done!  Your caregiver has been notified.", 'success'
            )
            pygame.display.flip()

    # ------------------------------------------------------------------
    # Dosage retry screen
    # ------------------------------------------------------------------

    def show_dosage_retry_screen(
        self,
        medicine_name: str,
        taken: int,
        required: int,
        attempt: int,
        max_attempts: int,
    ):
        """
        Shown when the patient has removed the wrong number of pills and a
        retry is available.

        Parameters
        ----------
        medicine_name : display name of the medicine
        taken         : cumulative pills detected so far this dose window
        required      : total pills required for the dose
        attempt       : current attempt number (1-based)
        max_attempts  : maximum attempts allowed before aborting
        """
        if not self.initialized:
            return

        remaining = required - taken

        with self.screen_lock:
            self._draw_frame("INCORRECT DOSAGE", 'warning')
            self._draw_card('warning')

            cy = self._card_text_y(10)

            # Medicine name
            self._draw_text(medicine_name, 'large', 'text_dark',
                            self.width // 2, cy, center=True)

            # Pill count summary bar
            bar_y = cy + 72
            cell_w = 64
            cell_h = 64
            gap    = 12
            total_bar_w = required * cell_w + (required - 1) * gap
            bar_x = (self.width - total_bar_w) // 2

            for i in range(required):
                x = bar_x + i * (cell_w + gap)
                color = 'success' if i < taken else 'text_light'
                self._draw_rect(color, x, bar_y, cell_w, cell_h, radius=10)
                label = str(i + 1)
                self._draw_text(label, 'normal', 'white',
                                x + cell_w // 2, bar_y + cell_h // 2, center=True)

            # Counts
            count_y = bar_y + cell_h + 22
            self._draw_text(
                f"Detected:  {taken} pill(s)   |   Required:  {required} pill(s)",
                'normal', 'text_light', self.width // 2, count_y, center=True
            )

            # Main instruction
            instr_y = count_y + 52
            if remaining > 0:
                instr = (
                    f"Please take  {remaining}  more pill(s)"
                    if remaining > 1
                    else "Please take  1  more pill"
                )
            else:
                instr = "Correct amount detected - please wait..."
            self._draw_text(instr, 'medium', 'primary',
                            self.width // 2, instr_y, center=True)

            # Attempt counter
            attempt_y = instr_y + 52
            dots = "  ".join(
                ("●" if i < attempt else "○") for i in range(max_attempts)
            )
            self._draw_text(
                f"Attempt  {attempt} / {max_attempts}    {dots}",
                'small', 'text_light', self.width // 2, attempt_y, center=True
            )

            self._draw_footer(
                "Lift the bottle  |  Take the correct number  |  Replace the bottle",
                'warning'
            )
            pygame.display.flip()

    # ------------------------------------------------------------------
    # Overdose / too-many-pills screen
    # ------------------------------------------------------------------

    def show_overdose_screen(
        self,
        medicine_name: str,
        taken: int,
        required: int,
    ):
        """Shown when more pills than required have been detected."""
        if not self.initialized:
            return
        with self.screen_lock:
            self._draw_frame("TOO MANY PILLS", 'error')
            self._draw_card('error')

            cy = self._card_text_y(20)
            self._draw_text(medicine_name, 'large', 'text_dark',
                            self.width // 2, cy, center=True)

            self._draw_text(
                f"Detected  {taken}  pill(s)  —  only  {required}  required",
                'medium', 'error', self.width // 2, cy + 80, center=True
            )

            self._draw_text(
                "Do NOT take more medication.",
                'medium', 'text_dark', self.width // 2, cy + 140, center=True
            )
            self._draw_text(
                "Your caregiver will be notified immediately.",
                'normal', 'text_light', self.width // 2, cy + 192, center=True
            )

            self._draw_footer(
                "Please contact your caregiver.", 'error'
            )
            pygame.display.flip()

    # ------------------------------------------------------------------
    # Warning / verification-failed screen
    # ------------------------------------------------------------------

    def show_warning_screen(self, title: str, message: str):
        if not self.initialized:
            return
        with self.screen_lock:
            self._draw_frame("VERIFICATION FAILED", 'error')
            self._draw_card('error')

            cy = self._card_text_y(20)
            self._draw_text(title, 'large', 'text_dark',
                            self.width // 2, cy, center=True)

            detail_y = cy + 80
            for line in self._wrap_text(message, 'medium', max_width=760):
                self._draw_text(line, 'medium', 'text_dark',
                                self.width // 2, detail_y, center=True)
                detail_y += 46

            self._draw_text(
                "Please check the bottle, tag, and dosage.",
                'normal', 'text_light',
                self.width // 2, self._PANEL_BOT - 60, center=True
            )

            self._draw_footer(
                "Press SPACE to continue", 'error'
            )
            pygame.display.flip()

    # ------------------------------------------------------------------
    # Security alert screen
    # ------------------------------------------------------------------

    def show_security_alert_screen(self, issues: list):
        """
        Show a structured alert for one or more security violations.

        Each entry in *issues* is a dict with keys:
          station_label  : human-readable station name, e.g. "Station 1"
          medicine_name  : registered medicine name
          issue          : "missing" | "incorrect" | "tampered"
          scheduled_time : dose time string, e.g. "08:00"
          tamper_delta_g : (tampered only) weight discrepancy in grams
          tamper_pills_est : (tampered only) estimated pills removed
        """
        if not self.initialized:
            return
        with self.screen_lock:
            self._draw_frame("SECURITY ALERT", 'error')
            self._draw_card('error')

            cy = self._card_text_y(0)

            if not issues:
                self._draw_text(
                    "Security violation detected",
                    'large', 'error', self.width // 2, cy + 40, center=True
                )
                self._draw_footer("Please check all medication stations.", 'error')
                pygame.display.flip()
                return

            # Row layout – up to 3 issues fit comfortably
            row_h      = min(130, (self._PANEL_BOT - cy - 20) // max(len(issues), 1))
            row_y      = cy
            left_x     = self._PANEL_X + 24
            right_x    = self._PANEL_X + self._PANEL_W - 24

            for item in issues[:3]:
                station_label  = item.get("station_label", "Unknown Station")
                medicine_name  = item.get("medicine_name", "Unknown Medicine")
                issue          = item.get("issue", "missing")
                scheduled_time = item.get("scheduled_time", "")

                # Row tint: amber = missing, red = incorrect/tampered
                strip_color = (
                    (255, 193, 7, 40) if issue == "missing"
                    else (220, 53, 69, 40)
                )
                strip_surf = pygame.Surface(
                    (self._PANEL_W - 16, row_h - 8), pygame.SRCALPHA
                )
                strip_surf.fill(strip_color)
                self.screen.blit(strip_surf, (self._PANEL_X + 8, row_y))

                # Medicine name – medium, left
                self._draw_text(
                    medicine_name, 'medium', 'text_dark',
                    left_x, row_y + 10
                )

                # Issue badge – right side
                if issue == "missing":
                    badge_text  = "BOTTLE REMOVED"
                    badge_color = 'warning'
                elif issue == "incorrect":
                    badge_text  = "WRONG BOTTLE"
                    badge_color = 'error'
                elif issue == "tampered":
                    badge_text  = "TAMPERING DETECTED"
                    badge_color = 'error'
                else:
                    badge_text  = "ALERT"
                    badge_color = 'error'
                self._draw_text(
                    badge_text, 'small', badge_color,
                    right_x, row_y + 14, right=True
                )

                # Station + time – second line, left
                detail = station_label
                if issue != "tampered" and scheduled_time:
                    detail += f"   •   Due at  {scheduled_time}"
                if issue == "tampered":
                    delta_g   = item.get("tamper_delta_g", 0.0)
                    pills_est = item.get("tamper_pills_est", "?")
                    detail += f"   •   {delta_g:.1f} g lighter  (~{pills_est} pills)"
                self._draw_text(detail, 'normal', 'text_light', left_x, row_y + 46)

                # Action instruction – third line, left
                if issue == "missing":
                    action = f"Please return {medicine_name} to {station_label}"
                elif issue == "incorrect":
                    action = f"Replace bottle with {medicine_name} on {station_label}"
                else:  # tampered
                    action = "Caregiver has been notified. Do not take any more pills."
                self._draw_text(action, 'normal', 'primary', left_x, row_y + 78)

                # Divider between rows
                if item is not issues[-1]:
                    pygame.draw.line(
                        self.screen, self.colors['text_light'],
                        (self._PANEL_X + 24, row_y + row_h - 6),
                        (self._PANEL_X + self._PANEL_W - 24, row_y + row_h - 6), 1
                    )

                row_y += row_h

            has_tamper = any(i.get("issue") == "tampered" for i in issues[:3])
            footer_text = (
                "Possible tampering detected — caregiver alerted."
                if has_tamper
                else "Medication must remain on the station until dose time."
            )
            self._draw_footer(footer_text, 'error')
            pygame.display.flip()

    # ------------------------------------------------------------------
    # Error screen
    # ------------------------------------------------------------------

    def show_error_screen(self, error_message: str):
        if not self.initialized:
            return
        with self.screen_lock:
            self._draw_frame("SYSTEM ALERT", 'error')
            self._draw_card('error')

            cy = self._card_text_y(30)
            self._draw_text("An error has occurred", 'large', 'error',
                            self.width // 2, cy, center=True)

            detail_y = cy + 90
            for line in self._wrap_text(str(error_message), 'medium', max_width=760):
                self._draw_text(line, 'medium', 'text_dark',
                                self.width // 2, detail_y, center=True)
                detail_y += 46

            self._draw_footer(
                "Please restart the system or contact support.", 'error'
            )
            pygame.display.flip()

    # ------------------------------------------------------------------
    # Instruction screen
    # ------------------------------------------------------------------

    def show_instruction_screen(self, title: str, instructions,
                                  footer: str = "Press SPACE to continue"):
        """
        Show stage instructions on screen.
        instructions: list of strings (prefix with '  ' for smaller lines)
        """
        if not self.initialized:
            return
        with self.screen_lock:
            self._draw_frame(title, 'primary')
            self._draw_card('primary')

            y = self._card_text_y(14)
            for line in instructions:
                if line == '':
                    y += 18
                elif line.startswith('  '):
                    self._draw_text(line.strip(), 'normal', 'text_light',
                                    self.width // 2, y, center=True)
                    y += 34
                else:
                    self._draw_text(line, 'medium', 'text_dark',
                                    self.width // 2, y, center=True)
                    y += 50

            self._draw_footer(footer, 'primary')
            pygame.display.flip()

    # ------------------------------------------------------------------
    # Selection screen
    # ------------------------------------------------------------------

    def show_selection_screen(self, title: str, items,
                               selected_idx: int,
                               footer: str = "UP/DOWN to select   SPACE to confirm"):
        """Show a list of selectable items."""
        if not self.initialized:
            return
        with self.screen_lock:
            self._draw_frame(title, 'secondary')
            self._draw_card('secondary')

            y     = self._card_text_y(14)
            row_h = 56
            for i, item in enumerate(items):
                if i == selected_idx:
                    self._draw_rect(
                        'secondary',
                        self._PANEL_X + 20, y - 20,
                        self._PANEL_W - 40, 44, radius=8
                    )
                    self._draw_text(f">  {item}", 'medium', 'white',
                                    self.width // 2, y, center=True)
                else:
                    self._draw_text(item, 'medium', 'text_light',
                                    self.width // 2, y, center=True)
                y += row_h

            self._draw_footer(footer, 'secondary')
            pygame.display.flip()

    # ------------------------------------------------------------------
    # Event helpers (unchanged)
    # ------------------------------------------------------------------

    def pump_events(self):
        """
        Drain the pygame event queue and tick the clock.
        Returns the last key pressed (pygame constant) or None.
        MUST be called from the main thread only.
        """
        if not self.initialized:
            return None
        key_pressed = None
        for event in pygame.event.get():
            if event.type == pygame.KEYDOWN:
                key_pressed = event.key
            elif event.type == pygame.QUIT:
                key_pressed = pygame.K_ESCAPE
        self.clock.tick(30)
        return key_pressed

    def update(self):
        """
        Legacy update call – drains events and ticks clock.
        MUST be called from the main thread only.
        """
        if self.initialized:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pass
            self.clock.tick(30)

    def cleanup(self):
        if self.initialized:
            try:
                pygame.quit()
            except Exception as e:
                self.logger.warning(f"Display cleanup warning: {e}")
            finally:
                self.initialized = False
            self.logger.info("Display cleanup complete")
