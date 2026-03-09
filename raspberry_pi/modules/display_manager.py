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
            'background': (240, 240, 245), 'primary': (44, 95, 126),
            'secondary': (74, 144, 164), 'success': (82, 183, 136),
            'warning': (244, 162, 97), 'error': (232, 93, 117),
            'text_dark': (43, 45, 66), 'text_light': (141, 153, 174),
            'white': (255, 255, 255)
        }
        
        self.screen = None
        self.clock = None
        self.initialized = False
        self.current_screen = 'idle'
        self.screen_data = {}
        self.screen_lock = Lock()
        self.fonts = {}
        
    def initialize(self):
        try:
            pygame.init()
            if self.fullscreen:
                self.screen = pygame.display.set_mode((self.width, self.height), pygame.FULLSCREEN)
            else:
                self.screen = pygame.display.set_mode((self.width, self.height))
            self.clock = pygame.time.Clock()
            self.fonts = {
                'huge': pygame.font.Font(None, 72), 'large': pygame.font.Font(None, 48),
                'medium': pygame.font.Font(None, 32), 'normal': pygame.font.Font(None, 24)
            }
            self.initialized = True
            self.show_idle_screen()
            return True
        except Exception as e:
            self.logger.error(f"Display init failed: {e}")
            return False
    
    def _draw_text(self, text, font_key, color, x, y, center=False):
        font = self.fonts[font_key]
        surf = font.render(text, True, self.colors[color])
        if center:
            rect = surf.get_rect(center=(x, y))
        else:
            rect = surf.get_rect(topleft=(x, y))
        self.screen.blit(surf, rect)
    
    def show_idle_screen(self, next_medication=None):
        with self.screen_lock:
            self.current_screen = 'idle'
            self.screen_data = {'next_medication': next_medication}
        if self.initialized:
            self.screen.fill(self.colors['background'])
            time_str = datetime.now().strftime('%H:%M')
            self._draw_text(time_str, 'huge', 'primary', self.width//2, 150, True)
            pygame.display.flip()
    
    def show_reminder_screen(self, medicine_name, dosage, time_str):
        with self.screen_lock:
            self.current_screen = 'reminder'
            self.screen_data = {'medicine_name': medicine_name, 'dosage': dosage}
        if self.initialized:
            self.screen.fill(self.colors['primary'])
            self._draw_text("TIME FOR MEDICATION", 'large', 'white', self.width//2, 100, True)
            self._draw_text(medicine_name, 'huge', 'white', self.width//2, 250, True)
            self._draw_text(f"{dosage} pill(s)", 'large', 'white', self.width//2, 350, True)
            pygame.display.flip()
    
    def show_monitoring_screen(self, elapsed, duration, status="Monitoring..."):
        if self.initialized:
            self.screen.fill(self.colors['background'])
            self._draw_text("MONITORING", 'large', 'primary', self.width//2, 100, True)
            progress = elapsed / duration if duration > 0 else 0
            bar_w, bar_h = 600, 40
            bar_x, bar_y = (self.width - bar_w) // 2, 300
            pygame.draw.rect(self.screen, self.colors['text_light'], (bar_x, bar_y, bar_w, bar_h), border_radius=20)
            fill_w = int(bar_w * min(progress, 1.0))
            if fill_w > 0:
                pygame.draw.rect(self.screen, self.colors['secondary'], (bar_x, bar_y, fill_w, bar_h), border_radius=20)
            self._draw_text(f"{int(duration-elapsed)}s left", 'large', 'text_light', self.width//2, 370, True)
            pygame.display.flip()
            
    def show_success_screen(self, medicine_name, message="Success!"):
        if self.initialized:
            self.screen.fill(self.colors['background'])
            self._draw_text("?", 'huge', 'success', self.width//2, 150, True)
            self._draw_text(message, 'large', 'success', self.width//2, 280, True)
            pygame.display.flip()
    
    def show_warning_screen(self, title, message):
        if self.initialized:
            self.screen.fill(self.colors['background'])
            self._draw_text("?", 'huge', 'warning', self.width//2, 120, True)
            self._draw_text(title, 'large', 'warning', self.width//2, 240, True)
            pygame.display.flip()
    
    def show_error_screen(self, error_message):
        if self.initialized:
            self.screen.fill(self.colors['background'])
            self._draw_text("?", 'huge', 'error', self.width//2, 120, True)
            self._draw_text("ERROR", 'large', 'error', self.width//2, 240, True)
            pygame.display.flip()
    
    def update(self):
        if self.initialized:
            self.clock.tick(30)
    
    def cleanup(self):
        if self.initialized:
            pygame.quit()
            self.initialized = False
