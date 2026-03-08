"""
Smart Medication System - Weight Manager Module

Handles weight sensor data from M5StickC stations via MQTT.
Processes weight changes, detects pill removal events, and estimates pill counts.
"""

import time
from typing import Dict, Any, Callable, Optional
from collections import deque


class WeightManager:
    """Manages weight sensor data and pill counting"""
    
    def __init__(self, config: dict, logger):
        """
        Initialize weight manager
        
        Args:
            config: Weight sensor configuration
            logger: Logger instance
        """
        self.config = config
        self.logger = logger
        
        # Weight data storage (per station)
        self.weight_data = {}  # {station_id: latest_weight_data}
        self.weight_history = {}  # {station_id: deque of recent weights}
        self.baseline_weights = {}  # {station_id: baseline_weight_g}
        
        # Event callbacks
        self.pill_removal_callback = None
        self.pill_addition_callback = None
        
        # Configuration per station
        self.station_configs = {}
        for station_key, station_config in config.items():
            if isinstance(station_config, dict) and 'id' in station_config:
                station_id = station_config['id']
                self.station_configs[station_id] = station_config
                self.weight_history[station_id] = deque(maxlen=10)  # Keep last 10 readings
        
        self.logger.info(f"Weight manager initialized for {len(self.station_configs)} stations")
    
    def process_weight_data(self, data: Dict[str, Any]):
        """
        Process incoming weight data from M5StickC
        
        Args:
            data: Weight data dictionary containing:
                - station_id: Station identifier
                - weight_g: Current weight in grams
                - stable: Stability flag
                - delta_g: Change from baseline
                - timestamp: Unix timestamp
                - pills_estimated: Estimated pill count (optional)
        """
        try:
            station_id = data.get('station_id')
            weight_g = data.get('weight_g')
            stable = data.get('stable', False)
            delta_g = data.get('delta_g', 0)
            timestamp = data.get('timestamp', time.time())
            
            if not station_id:
                self.logger.warning("Received weight data without station_id")
                return
            
            # Store latest data
            self.weight_data[station_id] = data
            self.weight_history[station_id].append({
                'weight_g': weight_g,
                'timestamp': timestamp,
                'stable': stable
            })
            
            self.logger.debug(f"Weight data from {station_id}: {weight_g}g (delta: {delta_g}g, stable: {stable})")
            
            # Detect pill removal/addition events
            if stable and abs(delta_g) > self._get_threshold(station_id):
                self._detect_pill_event(station_id, delta_g, weight_g)
            
        except Exception as e:
            self.logger.error(f"Error processing weight data: {e}")
    
    def _get_threshold(self, station_id: str) -> float:
        """Get delta threshold for station"""
        config = self.station_configs.get(station_id, {})
        return config.get('threshold_delta_g', 0.2)
    
    def _get_pill_weight(self, station_id: str) -> float:
        """Get average pill weight for station in grams"""
        config = self.station_configs.get(station_id, {})
        pill_weight_mg = config.get('pill_weight_mg', 500)
        return pill_weight_mg / 1000.0  # Convert to grams
        
    def _detect_pill_event(self, station_id: str, delta_g: float, current_weight_g: float):
        """
        Detect pill removal or addition event
        
        Args:
            station_id: Station ID
            delta_g: Weight change in grams
            current_weight_g: Current weight in grams
        """
        pill_weight = self._get_pill_weight(station_id)
        
        # Estimate number of pills changed
        pills_changed = round(abs(delta_g) / pill_weight)
        
        if delta_g < 0:  # Pills removed
            self.logger.info(f"Pill removal detected on {station_id}: {pills_changed} pills ({abs(delta_g):.2f}g)")
            
            event_data = {
                'station_id': station_id,
                'pills_removed': pills_changed,
                'weight_change_g': abs(delta_g),
                'current_weight_g': current_weight_g,
                'timestamp': time.time()
            }
            
            if self.pill_removal_callback:
                try:
                    self.pill_removal_callback(event_data)
                except Exception as e:
                    self.logger.error(f"Error in pill removal callback: {e}")
        
        elif delta_g > 0:  # Pills added
            self.logger.info(f"Pill addition detected on {station_id}: {pills_changed} pills ({delta_g:.2f}g)")
            
            event_data = {
                'station_id': station_id,
                'pills_added': pills_changed,
                'weight_change_g': delta_g,
                'current_weight_g': current_weight_g,
                'timestamp': time.time()
            }
            
            if self.pill_addition_callback:
                try:
                    self.pill_addition_callback(event_data)
                except Exception as e:
                    self.logger.error(f"Error in pill addition callback: {e}")
    
    def set_baseline_weight(self, station_id: str, weight_g: float):
        """
        Set baseline weight for a station
        
        Args:
            station_id: Station ID
            weight_g: Baseline weight in grams
        """
        self.baseline_weights[station_id] = weight_g
        self.logger.info(f"Baseline weight set for {station_id}: {weight_g}g")
    
    def get_current_weight(self, station_id: str) -> Optional[float]:
        """
        Get current weight for a station
        
        Args:
            station_id: Station ID
            
        Returns:
            Current weight in grams or None if not available
        """
        data = self.weight_data.get(station_id)
        return data.get('weight_g') if data else None
    
    def is_stable(self, station_id: str) -> bool:
        """
        Check if weight reading is stable for a station
        
        Args:
            station_id: Station ID
            
        Returns:
            True if stable
        """
        data = self.weight_data.get(station_id)
        return data.get('stable', False) if data else False
        
    def estimate_pill_count(self, station_id: str, weight_g: float) -> int:
        """
        Estimate number of pills based on weight
        
        Args:
            station_id: Station ID
            weight_g: Weight in grams
            
        Returns:
            Estimated pill count
        """
        pill_weight = self._get_pill_weight(station_id)
        if pill_weight > 0:
            return round(weight_g / pill_weight)
        return 0
    
    def verify_dosage(self, station_id: str, expected_pills: int, tolerance: int = 1) -> Dict[str, Any]:
        """
        Verify if correct number of pills were taken
        
        Args:
            station_id: Station ID
            expected_pills: Expected number of pills
            tolerance: Acceptable deviation (+-pills)
            
        Returns:
            Verification result dictionary
        """
        latest_data = self.weight_data.get(station_id)
        
        if not latest_data:
            return {
                'verified': False,
                'reason': 'No weight data available',
                'expected': expected_pills,
                'actual': None
            }
        
        delta_g = latest_data.get('delta_g', 0)
        if delta_g >= 0:  # No removal detected
            return {
                'verified': False,
                'reason': 'No pill removal detected',
                'expected': expected_pills,
                'actual': 0
            }
        
        # Estimate pills removed
        pill_weight = self._get_pill_weight(station_id)
        pills_removed = round(abs(delta_g) / pill_weight)
        
        # Check if within tolerance
        difference = abs(pills_removed - expected_pills)
        verified = difference <= tolerance
        
        return {
            'verified': verified,
            'expected': expected_pills,
            'actual': pills_removed,
            'weight_change_g': abs(delta_g),
            'difference': difference,
            'within_tolerance': verified,
            'status': 'correct' if verified else 'incorrect'
        }
    
    def set_pill_removal_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """
        Set callback for pill removal events
        
        Args:
            callback: Function to call when pills are removed
        """
        self.pill_removal_callback = callback
        self.logger.info("Pill removal callback registered")
    
    def set_pill_addition_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """
        Set callback for pill addition events
        
        Args:
            callback: Function to call when pills are added
        """
        self.pill_addition_callback = callback
        self.logger.info("Pill addition callback registered")
        
    def get_station_status(self, station_id: str) -> Dict[str, Any]:
        """
        Get comprehensive status for a station
        
        Args:
            station_id: Station ID
            
        Returns:
            Status dictionary
        """
        data = self.weight_data.get(station_id)
        config = self.station_configs.get(station_id, {})
        
        if not data:
            return {
                'station_id': station_id,
                'status': 'no_data',
                'connected': False
            }
        
        # Check if data is recent (within last 30 seconds)
        time_since_update = time.time() - data.get('timestamp', 0)
        connected = time_since_update < 30
        
        return {
            'station_id': station_id,
            'connected': connected,
            'weight_g': data.get('weight_g'),
            'stable': data.get('stable', False),
            'medicine_name': config.get('medicine_name', 'Unknown'),
            'last_update': time_since_update,
            'status': 'online' if connected else 'offline'
        }
    
    def reset_station(self, station_id: str):
        """
        Reset/clear data for a station
        
        Args:
            station_id: Station ID
        """
        if station_id in self.weight_data:
            del self.weight_data[station_id]
        if station_id in self.weight_history:
            self.weight_history[station_id].clear()
        if station_id in self.baseline_weights:
            del self.baseline_weights[station_id]
        
        self.logger.info(f"Reset station data for {station_id}")
