"""
Smart Medication System - Display Manager Module
"""
import pygame
from datetime import datetime
from threading import Lock

class DisplayManager:

    def __init__(self, config: dict, logger):
        self.config = config
        self.logger = logger
        self.width = config.get('width', 1024)
        self.height = config.get('height', 600)
        self.fullscreen = config.get('fullscreen', True)

        self.colors = {
            'background':  (240, 240, 245),
            'primary':     (44,  95,  126),
            'secondary':   (74,  144, 164),
            'success':     (82,  183, 136),
            'warning':     (244, 162, 97),
            'error':       (232, 93,  117),
            'text_dark':   (43,  45,  66),
            'text_light':  (141, 153, 174),
            'white':       (255, 255, 255),
            'black':       (0,   0,   0),
        }

        self.screen = None
        self.clock = None
        self.initialized = False
        self.screen_lock = Lock()
        self.fonts = {}

    def initialize(self) -> bool:
        try:
            pygame.init()
            flags = pygame.FULLSCREEN if self.fullscreen else 0
            self.screen = pygame.display.set_mode((self.width, self.height), flags)
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
            
    def _draw_text(self, text, font_key, color_key, x, y, center=False):
        font = self.fonts[font_key]
        color = self.colors[color_key]
        surface = font.render(str(text), True, color)
        rect = surface.get_rect(center=(x, y)) if center else surface.get_rect(topleft=(x, y))
        self.screen.blit(surface, rect)
        
    def _wrap_text(self, text, font_key='normal', max_width=760):
        font = self.fonts[font_key]
        words = str(text).split()
        lines = []
        current = ""

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

    def _draw_rect(self, color_key, x, y, w, h, radius=0):
        pygame.draw.rect(self.screen, self.colors[color_key], (x, y, w, h), border_radius=radius)

    def _fill(self, color_key):
        self.screen.fill(self.colors[color_key])

    # ------------------------------------------------------------------ #
    # NEW: Instruction screen - replaces input() prompts
    # ------------------------------------------------------------------ #

    def show_instruction_screen(self, title, instructions, footer="Press SPACE to continue"):
        """
        Show stage instructions on screen.
        User presses SPACE/ENTER on the keyboard to proceed.
        instructions: list of strings (prefix with '  ' for smaller/indented lines)
        """
        if not self.initialized:
            return
        with self.screen_lock:
            self._fill('background')

            # Top accent bar
            self._draw_rect('secondary', 0, 0, self.width, 8)

            # Title
            self._draw_text(title, 'large', 'primary', self.width // 2, 65, center=True)

            pygame.draw.line(
                self.screen, self.colors['text_light'],
                (80, 105), (self.width - 80, 105), 1
            )

            # Instructions
            y = 150
            for line in instructions:
                if line.startswith('  '):
                    # Indented = smaller text
                    self._draw_text(line.strip(), 'normal', 'text_light',
                                    self.width // 2, y, center=True)
                    y += 36
                elif line == '':
                    y += 20
                else:
                    self._draw_text(line, 'medium', 'text_dark',
                                    self.width // 2, y, center=True)
                    y += 52
                    
            # Footer bar
            self._draw_rect('primary', 0, self.height - 60, self.width, 60)
            self._draw_text(footer, 'medium', 'white',
                            self.width // 2, self.height - 30, center=True)

            pygame.display.flip()

    # ------------------------------------------------------------------ #
    # NEW: Watching screen - live weight during pill removal phase
    # ------------------------------------------------------------------ #

    def show_watching_screen(self, title, weight_g, stable, baseline_g,
                              phase, armed, elapsed=0, timeout=90):
        """
        Live weight display shown while waiting for pill removal.
        Updates every loop iteration to show current scale reading.
        """
        if not self.initialized:
            return
        with self.screen_lock:
            self._fill('background')

            self._draw_text(title, 'medium', 'primary', self.width // 2, 38, center=True)

            pygame.draw.line(
                self.screen, self.colors['text_light'],
                (80, 64), (self.width - 80, 64), 1
            )

            # Large weight readout
            weight_color = 'success' if stable else 'warning'
            self._draw_text(f"{weight_g:.2f} g", 'huge', weight_color,
                            self.width // 2, 180, center=True)

            stable_text = "STABLE" if stable else "SETTLING..."
            self._draw_text(stable_text, 'medium', weight_color,
                            self.width // 2, 255, center=True)

            # Info grid
            info_y = 310
            for label, value in [
                ("Baseline", f"{baseline_g:.2f} g"),
                ("Phase",    phase.replace('_', ' ')),
                ("Armed",    "YES" if armed else "NO"),
            ]:
                line = f"{label}:  {value}"
                self._draw_text(line, 'normal', 'text_light',
                                self.width // 2, info_y, center=True)
                info_y += 36
                
            # Progress bar
            bar_w = 640
            bar_h = 18
            bar_x = (self.width - bar_w) // 2
            bar_y = 450

            self._draw_rect('text_light', bar_x, bar_y, bar_w, bar_h, radius=9)
            progress = min(elapsed / max(timeout, 1), 1.0)
            fill_w = int(bar_w * progress)
            if fill_w > 0:
                pygame.draw.rect(
                    self.screen, self.colors['secondary'],
                    (bar_x, bar_y, fill_w, bar_h), border_radius=9
                )

            remaining = max(0, int(timeout - elapsed))
            self._draw_text(
                f"Watching for pill removal...   {remaining}s remaining",
                'normal', 'text_light', self.width // 2, 490, center=True
            )

            self._draw_text(
                "Lift bottle, remove pills, place bottle back, leave still",
                'small', 'text_light', self.width // 2, 528, center=True
            )

            pygame.display.flip()

    # ------------------------------------------------------------------ #
    # NEW: Medicine selection screen
    # ------------------------------------------------------------------ #

    def show_selection_screen(self, title, items, selected_idx, footer="UP/DOWN to select   SPACE to confirm"):
        """
        Show a list of selectable items.
        items: list of strings describing each option.
        selected_idx: currently highlighted index.
        """
        if not self.initialized:
            return
        with self.screen_lock:
            self._fill('background')
            
            self._draw_text(title, 'large', 'primary', self.width // 2, 65, center=True)

            pygame.draw.line(
                self.screen, self.colors['text_light'],
                (80, 105), (self.width - 80, 105), 1
            )

            y = 155
            row_h = 58
            for i, item in enumerate(items):
                if i == selected_idx:
                    self._draw_rect('secondary', 80, y - 22, self.width - 160, 44, radius=8)
                    self._draw_text(f">  {item}", 'medium', 'white',
                                    self.width // 2, y, center=True)
                else:
                    self._draw_text(item, 'medium', 'text_light',
                                    self.width // 2, y, center=True)
                y += row_h

            self._draw_rect('primary', 0, self.height - 60, self.width, 60)
            self._draw_text(footer, 'medium', 'white',
                            self.width // 2, self.height - 30, center=True)

            pygame.display.flip()

    # ------------------------------------------------------------------ #
    # NEW: Pipeline status screen (shown during verification)
    # ------------------------------------------------------------------ #

    def show_pipeline_screen(self, stage_name, detail="Please wait..."):
        if not self.initialized:
            return

        with self.screen_lock:
            self._fill('background')

            panel_x = 80
            panel_y = 70
            panel_w = self.width - 160
            panel_h = self.height - 140

            self._draw_rect('white', panel_x, panel_y, panel_w, panel_h, radius=20)
            self._draw_rect('primary', panel_x, panel_y, panel_w, 14, radius=20)

            self._draw_text(
                "SMART MEDICATION VERIFICATION",
                'large',
                'primary',
                self.width // 2,
                135,
                center=True
            )

            self._draw_text(
                stage_name,
                'xlarge' if 'xlarge' in self.fonts else 'large',
                'text_dark',
                self.width // 2,
                235,
                center=True
            )

            y = 320
            for line in self._wrap_text(detail, font_key='medium', max_width=640):
                self._draw_text(
                    line,
                    'medium',
                    'text_dark',
                    self.width // 2,
                    y,
                    center=True
                )
                y += 40

            self._draw_text(
                "Processing...",
                'normal',
                'secondary',
                self.width // 2,
                470,
                center=True
            )

            pygame.display.flip()

    # ------------------------------------------------------------------ #
    # Existing screens (unchanged)
    # ------------------------------------------------------------------ #

    def _normalize_idle_screen_data(self, idle_data):
        next_medication = None
        today_schedule = []

        if isinstance(idle_data, dict):
            if "next_medication" in idle_data or "today_schedule" in idle_data:
                next_medication = idle_data.get("next_medication")
                today_schedule = idle_data.get("today_schedule") or []
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
            self._fill('background')

            next_medication, today_schedule = self._normalize_idle_screen_data(
                next_medication
            )
            time_str = datetime.now().strftime('%H:%M')
            date_str = datetime.now().strftime('%A, %d %B %Y')

            self._draw_text(time_str, 'large', 'primary', self.width // 2, 70, center=True)
            self._draw_text(date_str, 'normal', 'text_light', self.width // 2, 115, center=True)

            header_x = 70
            header_y = 150
            header_w = self.width - 140
            header_h = 110
            self._draw_rect('white', header_x, header_y, header_w, header_h, radius=18)
            self._draw_rect('secondary', header_x, header_y, header_w, 10, radius=18)

            self._draw_text(
                "Next Medication",
                'small',
                'text_light',
                header_x + 32,
                header_y + 26
            )

            if next_medication:
                name = next_medication.get('medicine_name', 'Unknown')
                time_val = next_medication.get('time', '')
                station_val = next_medication.get('station_id', '')
                summary = f"at {time_val}" if time_val else "Scheduled"
                if station_val:
                    summary = f"{summary} on {station_val.replace('_', ' ').title()}"

                self._draw_text(
                    name,
                    'medium',
                    'primary',
                    header_x + 32,
                    header_y + 58
                )
                self._draw_text(
                    summary,
                    'normal',
                    'secondary',
                    header_x + 32,
                    header_y + 86
                )
            else:
                self._draw_text(
                    "No medications scheduled",
                    'medium',
                    'text_light',
                    header_x + 32,
                    header_y + 62
                )

            panel_x = 70
            panel_y = 285
            panel_w = self.width - 140
            panel_h = 250

            self._draw_rect('white', panel_x, panel_y, panel_w, panel_h, radius=20)
            self._draw_rect('primary', panel_x, panel_y, panel_w, 10, radius=20)
            self._draw_text(
                "Today's Timetable",
                'normal',
                'primary',
                panel_x + 28,
                panel_y + 24
            )

            if today_schedule:
                row_y = panel_y + 62
                row_h = 34
                max_rows = 5

                for idx, item in enumerate(today_schedule[:max_rows]):
                    if idx > 0:
                        pygame.draw.line(
                            self.screen,
                            self.colors['background'],
                            (panel_x + 24, row_y - 14),
                            (panel_x + panel_w - 24, row_y - 14),
                            1
                        )

                    station_label = str(item.get('station_id', '')).replace('_', ' ').title()
                    details = item.get('medicine_name', 'Unknown')
                    if station_label:
                        details = f"{details}  ({station_label})"

                    self._draw_text(
                        item.get('time', '--:--'),
                        'normal',
                        'secondary',
                        panel_x + 28,
                        row_y
                    )
                    self._draw_text(
                        details,
                        'normal',
                        'text_dark',
                        panel_x + 150,
                        row_y
                    )
                    row_y += row_h

                if len(today_schedule) > max_rows:
                    remaining = len(today_schedule) - max_rows
                    self._draw_text(
                        f"+ {remaining} more scheduled dose(s)",
                        'small',
                        'text_light',
                        panel_x + 28,
                        panel_y + panel_h - 28
                    )
            else:
                self._draw_text(
                    "No registered medicine timetable available yet",
                    'normal',
                    'text_light',
                    self.width // 2,
                    panel_y + 120,
                    center=True
                )

            self._draw_text("Smart Medication System", 'small', 'text_light',
                            self.width // 2, 570, center=True)
            pygame.display.flip()

    def show_reminder_screen(self, medicine_name, dosage, time_str):
        if not self.initialized:
            return
        with self.screen_lock:
            self._fill('primary')
            
            self._draw_text("TIME FOR MEDICATION", 'large', 'white',
                            self.width // 2, 80, center=True)
            pygame.draw.line(self.screen, self.colors['secondary'],
                             (80, 130), (self.width - 80, 130), 2)

            self._draw_text(medicine_name, 'huge', 'white',
                            self.width // 2, 230, center=True)
            self._draw_text(f"Take {dosage} pill(s)", 'large', 'white',
                            self.width // 2, 340, center=True)
            self._draw_text(f"Scheduled at {time_str}", 'medium', 'white',
                            self.width // 2, 410, center=True)

            pygame.draw.line(self.screen, self.colors['secondary'],
                             (80, 460), (self.width - 80, 460), 2)
            self._draw_text(
                "Remove pills from bottle, then place bottle back on scale",
                'normal', 'white', self.width // 2, 500, center=True
            )
            pygame.display.flip()

    def show_monitoring_screen(self, elapsed, duration, message="Monitoring intake..."):
        if not self.initialized:
            return
        with self.screen_lock:
            self._fill('background')

            self._draw_text("MONITORING INTAKE", 'large', 'primary',
                            self.width // 2, 80, center=True)
            self._draw_text(message, 'normal', 'text_light',
                            self.width // 2, 135, center=True)

            self._draw_text(
                "Bring your hand to your mouth and open your mouth clearly",
                'normal', 'secondary', self.width // 2, 175, center=True
            )

            bar_w = 700
            bar_h = 36
            bar_x = (self.width - bar_w) // 2
            bar_y = 260

            self._draw_rect('text_light', bar_x, bar_y, bar_w, bar_h, radius=18)
            progress = min(elapsed / duration, 1.0) if duration > 0 else 0
            fill_w = int(bar_w * progress)
            if fill_w > 0:
                pygame.draw.rect(
                    self.screen, self.colors['secondary'],
                    (bar_x, bar_y, fill_w, bar_h), border_radius=18
                )

            pct = int(progress * 100)
            self._draw_text(f"{pct}%", 'medium', 'text_dark',
                            self.width // 2, bar_y + bar_h + 30, center=True)

            remaining = max(0, int(duration - elapsed))
            self._draw_text(f"{remaining} seconds remaining", 'medium', 'text_light',
                            self.width // 2, 370, center=True)
            self._draw_text(
                "Please remain in front of the camera",
                'normal', 'text_light', self.width // 2, 430, center=True
            )

            pygame.display.flip()
            
    def show_success_screen(self, medicine_name, message="Medication taken successfully!"):
        if not self.initialized:
            return
        with self.screen_lock:
            self._fill('background')
            self._draw_rect('success', 0, 0, self.width, 12)

            self._draw_text("[SUCCESS]", 'large', 'success',
                            self.width // 2, 120, center=True)
            self._draw_text(medicine_name, 'huge', 'text_dark',
                            self.width // 2, 230, center=True)
            self._draw_text(message, 'medium', 'success',
                            self.width // 2, 330, center=True)
            self._draw_text(
                "Well done! Your caregiver has been notified.",
                'normal', 'text_light', self.width // 2, 400, center=True
            )

            time_str = datetime.now().strftime('%H:%M:%S')
            self._draw_text(f"Confirmed at {time_str}", 'small', 'text_light',
                            self.width // 2, 460, center=True)

            self._draw_text("Press SPACE to exit", 'normal', 'text_light',
                            self.width // 2, 540, center=True)

            self._draw_rect('success', 0, self.height - 12, self.width, 12)
            pygame.display.flip()

    def show_warning_screen(self, title, message):
        if not self.initialized:
            return

        with self.screen_lock:
            self._fill('background')

            panel_x = 70
            panel_y = 60
            panel_w = self.width - 140
            panel_h = self.height - 120

            self._draw_rect('white', panel_x, panel_y, panel_w, panel_h, radius=20)
            self._draw_rect('error', panel_x, panel_y, panel_w, 14, radius=20)

            self._draw_text(
                "VERIFICATION FAILED",
                'large',
                'error',
                self.width // 2,
                130,
                center=True
            )

            self._draw_text(
                title,
                'large',
                'text_dark',
                self.width // 2,
                220,
                center=True
            )

            y = 300
            for line in self._wrap_text(message, font_key='medium', max_width=660):
                self._draw_text(
                    line,
                    'medium',
                    'text_dark',
                    self.width // 2,
                    y,
                    center=True
                )
                y += 40

            self._draw_text(
                "Please check the bottle, tag, and dosage.",
                'normal',
                'text_light',
                self.width // 2,
                445,
                center=True
            )

            self._draw_text(
                "Press SPACE to continue",
                'normal',
                'secondary',
                self.width // 2,
                505,
                center=True
            )

            pygame.display.flip()
            
    def show_error_screen(self, error_message):
        if not self.initialized:
            return
        with self.screen_lock:
            self._fill('background')
            self._draw_rect('error', 0, 0, self.width, 12)

            self._draw_text("[ERROR]", 'large', 'error',
                            self.width // 2, 120, center=True)
            self._draw_text("System Error", 'large', 'text_dark',
                            self.width // 2, 230, center=True)
            self._draw_text(str(error_message), 'normal', 'text_light',
                            self.width // 2, 310, center=True)
            self._draw_text(
                "Please restart the system or contact support.",
                'normal', 'text_light', self.width // 2, 380, center=True
            )

            self._draw_rect('error', 0, self.height - 12, self.width, 12)
            pygame.display.flip()

    def show_registration_screen(self, station_id, status_message="Waiting for medicine..."):
        if not self.initialized:
            return
        with self.screen_lock:
            self._fill('background')

            self._draw_text("MEDICINE REGISTRATION", 'large', 'primary',
                            self.width // 2, 80, center=True)

            pygame.draw.line(
                self.screen, self.colors['text_light'],
                (80, 130), (self.width - 80, 130), 2
            )

            self._draw_text(f"Station: {station_id}", 'medium', 'text_light',
                            self.width // 2, 180, center=True)
            self._draw_text(status_message, 'large', 'primary',
                            self.width // 2, 280, center=True)

            self._draw_text(
                "Place the medicine bottle on the scale",
                'normal', 'text_light', self.width // 2, 370, center=True
            )
            self._draw_text(
                "and tap the RFID tag to the reader",
                'normal', 'text_light', self.width // 2, 405, center=True
            )

            pygame.display.flip()
            
    def show_registration_success_screen(self, medicine_name, schedule_times):
        if not self.initialized:
            return
        with self.screen_lock:
            self._fill('background')
            self._draw_rect('success', 0, 0, self.width, 12)

            self._draw_text("REGISTERED", 'large', 'success',
                            self.width // 2, 100, center=True)
            self._draw_text(medicine_name, 'huge', 'text_dark',
                            self.width // 2, 210, center=True)
            self._draw_text("Medicine registered successfully", 'medium', 'success',
                            self.width // 2, 300, center=True)

            if schedule_times:
                times_str = "  ".join(schedule_times)
                self._draw_text(f"Scheduled at: {times_str}", 'medium', 'text_light',
                                self.width // 2, 370, center=True)

            self._draw_text(
                "Your caregiver has been notified.",
                'normal', 'text_light', self.width // 2, 440, center=True
            )

            self._draw_rect('success', 0, self.height - 12, self.width, 12)
            pygame.display.flip()

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
        Legacy update call - just drains events and ticks clock.
        Screen content is set by show_* methods (which call pygame.display.flip()).
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
