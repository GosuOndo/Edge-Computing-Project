"""
Smart Medication System - Logger Module

Provides centralized logging functionality with color-coded console output
and file logging with rotation.
"""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from datetime import datetime
import colorlog

class SystemLogger:
    """Centralized logging for the medication system"""
    
    def __init__(self, config: dict = None):
        """
        Initialize logger with configuration
        
        Args:
            config: Logging configuration dictionary
        """
        self.config = config or {}
        self.log_level = self.config.get('level', 'INFO')
        self.console_output = self.config.get('console_output', True)
        self.file_output = self.config.get('file_output', True)
        self.max_file_size_mb = self.config.get('max_file_size_mb', 10)
        self.backup_count = self.config.get('backup_count', 5)
        
        # Create logs directory if it doesn't exist
        self.log_dir = Path('logs')
        self.log_dir.mkdir(parents=True, exist_ok=True)
        (self.log_dir / 'events').mkdir(exist_ok=True)
        (self.log_dir / 'sensors').mkdir(exist_ok=True)
        (self.log_dir / 'system').mkdir(exist_ok=True)
        
        # Initialize loggers
        self.system_logger = self._create_logger('system', 'logs/system')
        self.event_logger = self._create_logger('events', 'logs/events')
        self.sensor_logger = self._create_logger('sensors', 'logs/sensors')
        
    def _create_logger(self, name: str, log_path: str) -> logging.Logger:
        """
        Create a logger with both console and file handlers
        
        Args:
            name: Logger name
            log_path: Path to log file (without extension)
            
        Returns:
            Configured logger instance
        """
        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, self.log_level))
        
        # Remove existing handlers to avoid duplicates
        logger.handlers = []
        
        # Console handler with colors
        if self.console_output:
            console_handler = colorlog.StreamHandler(sys.stdout)
            console_handler.setLevel(getattr(logging, self.log_level))
            
            console_format = colorlog.ColoredFormatter(
                '%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S',
                log_colors={
                    'DEBUG': 'cyan',
                    'INFO': 'green',
                    'WARNING': 'yellow',
                    'ERROR': 'red',
                    'CRITICAL': 'red,bg_white',
                }
            )
            console_handler.setFormatter(console_format)
            logger.addHandler(console_handler)
        
        # File handler with rotation
        if self.file_output:
            log_file = f"{log_path}/{datetime.now().strftime('%Y%m%d')}.log"
            Path(log_path).mkdir(parents=True, exist_ok=True)
            
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=self.max_file_size_mb * 1024 * 1024,
                backupCount=self.backup_count
            )
            file_handler.setLevel(getattr(logging, self.log_level))
            
            file_format = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            file_handler.setFormatter(file_format)
            logger.addHandler(file_handler)
        
        return logger
        
    def system(self, level: str, message: str, **kwargs):
        """
        Log system-level messages
        
        Args:
            level: Log level (debug, info, warning, error, critical)
            message: Log message
            **kwargs: Additional context to log
        """
        log_method = getattr(self.system_logger, level.lower())
        if kwargs:
            message = f"{message} | {kwargs}"
        log_method(message)
    
    def event(self, event_type: str, details: dict):
        """
        Log medication events
        
        Args:
            event_type: Type of event (reminder, intake, verification, alert)
            details: Event details dictionary
        """
        message = f"[{event_type.upper()}] {details}"
        self.event_logger.info(message)
    
    def sensor(self, sensor_type: str, data: dict):
        """
        Log sensor data
        
        Args:
            sensor_type: Type of sensor (weight, camera, etc.)
            data: Sensor data dictionary
        """
        message = f"[{sensor_type.upper()}] {data}"
        self.sensor_logger.debug(message)
    
    def info(self, message: str, **kwargs):
        """Log info message"""
        self.system('info', message, **kwargs)
    
    def debug(self, message: str, **kwargs):
        """Log debug message"""
        self.system('debug', message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        """Log warning message"""
        self.system('warning', message, **kwargs)
    
    def error(self, message: str, **kwargs):
        """Log error message"""
        self.system('error', message, **kwargs)
    
    def critical(self, message: str, **kwargs):
        """Log critical message"""
        self.system('critical', message, **kwargs)


# Global logger instance (singleton)
_logger_instance = None

def get_logger(config: dict = None) -> SystemLogger:
    """
    Get or create logger instance (singleton pattern)
    
    Args:
        config: Logger configuration (only used on first call)
        
    Returns:
        SystemLogger instance
    """
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = SystemLogger(config)
    return _logger_instance
