"""
Smart Medication System - Configuration Loader

Handles loading and validating system configuration from YAML files.
"""

import yaml
from pathlib import Path
from typing import Dict, Any
import os
from dotenv import load_dotenv

class ConfigLoader:
    """Load and manage system configuration"""
    
    def __init__(self, config_path: str = "config/config.yaml"):
        """
        Initialize config loader
        
        Args:
            config_path: Path to configuration file
        """
        self.config_path = Path(config_path)
        self.config = {}
        self.load_config()
        self.load_env_overrides()
    
    def load_config(self):
        """Load configuration from YAML file"""
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {self.config_path}\n"
                f"Please copy config.example.yaml to config.yaml and configure it."
            )
        
        with open(self.config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        self.validate_config()
    
    def load_env_overrides(self):
        """Load environment variable overrides (for secrets)"""
        load_dotenv()
        
        # Override Telegram token if set in environment
        telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        if telegram_token:
            self.config['telegram']['bot_token'] = telegram_token
        
        patient_chat_id = os.getenv('TELEGRAM_PATIENT_CHAT_ID')
        if patient_chat_id:
            self.config['telegram']['patient_chat_id'] = patient_chat_id
        
        caregiver_chat_id = os.getenv('TELEGRAM_CAREGIVER_CHAT_ID')
        if caregiver_chat_id:
            self.config['telegram']['caregiver_chat_id'] = caregiver_chat_id
    
    def validate_config(self):
        """Validate critical configuration parameters"""
        required_sections = [
            'system', 'hardware', 'mqtt', 'weight_sensors',
            'telegram', 'database', 'logging'
        ]
        
        for section in required_sections:
            if section not in self.config:
                raise ValueError(f"Missing required configuration section: {section}")
        
        # Validate Telegram configuration
        if self.config['telegram']['enabled']:
            if 'YOUR_BOT_TOKEN' in self.config['telegram']['bot_token']:
                raise ValueError(
                    "Telegram bot token not configured! "
                    "Please set TELEGRAM_BOT_TOKEN in .env or config.yaml"
                )
    
    def get(self, path: str, default: Any = None) -> Any:
        """
        Get configuration value by dot-separated path
        
        Args:
            path: Dot-separated path (e.g., 'mqtt.broker_host')
            default: Default value if path not found
            
        Returns:
            Configuration value
            
        Example:
            >>> config.get('mqtt.broker_host')
            'localhost'
        """
        keys = path.split('.')
        value = self.config
        
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        
        return value
    
    def set(self, path: str, value: Any):
        """
        Set configuration value by dot-separated path
        
        Args:
            path: Dot-separated path
            value: Value to set
        """
        keys = path.split('.')
        config = self.config
        
        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]
        
        config[keys[-1]] = value


    def save(self, path: str = None):
        """
        Save configuration to file
        
        Args:
            path: Path to save to (defaults to original path)
        """
        save_path = Path(path) if path else self.config_path
        
        with open(save_path, 'w') as f:
            yaml.dump(self.config, f, default_flow_style=False, sort_keys=False)
    
    def get_mqtt_config(self) -> Dict[str, Any]:
        """Get MQTT configuration"""
        return self.config['mqtt']
    
    def get_telegram_config(self) -> Dict[str, Any]:
        """Get Telegram configuration"""
        return self.config['telegram']
    
    def get_weight_sensor_config(self, station_id: str) -> Dict[str, Any]:
        """Get weight sensor configuration for specific station"""
        return self.config['weight_sensors'].get(station_id, {})
    
    def get_schedule(self) -> Dict[str, Any]:
        """
        Legacy helper for schedule config access.

        Runtime medication schedules are sourced from the database after
        onboarding, so this now returns an empty dict when no legacy schedule
        section exists.
        """
        return self.config.get('schedule', {})
    
    def get_logging_config(self) -> Dict[str, Any]:
        """Get logging configuration"""
        return self.config['logging']
    
    def is_offline_mode_enabled(self) -> bool:
        """Check if offline mode is enabled"""
        return self.config['network'].get('offline_mode_enabled', True)
    
    def __getitem__(self, key: str) -> Any:
        """Allow dictionary-style access"""
        return self.config[key]
    
    def __contains__(self, key: str) -> bool:
        """Check if key exists in config"""
        return key in self.config


# Global config instance (singleton)
_config_instance = None

def get_config(config_path: str = "config/config.yaml") -> ConfigLoader:
    """
    Get or create config instance (singleton pattern)
    
    Args:
        config_path: Path to configuration file (only used on first call)
        
    Returns:
        ConfigLoader instance
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = ConfigLoader(config_path)
    return _config_instance
