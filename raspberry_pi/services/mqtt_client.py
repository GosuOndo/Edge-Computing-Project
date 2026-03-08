"""
Smart Medication System - MQTT Client Service

Handles all MQTT communication with M5StickC weight sensor stations.
"""

import paho.mqtt.client as mqtt
import json
import time
from typing import Callable, Dict, Any
from threading import Thread, Event


class MQTTClient:
    """MQTT client for weight sensor communication"""
    
    def __init__(self, config: dict, logger):
        """
        Initialize MQTT client
        
        Args:
            config: MQTT configuration dictionary
            logger: Logger instance
        """
        self.config = config
        self.logger = logger
        
        self.broker_host = config['broker_host']
        self.broker_port = config['broker_port']
        self.client_id = config['client_id']
        self.topics = config['topics']
        self.qos = config['qos']
        self.keepalive = config['keepalive']
        
        # Callbacks
        self.weight_callback = None
        self.status_callback = None
        
        # Connection state
        self.connected = False
        self.reconnect_event = Event()
        self.reconnect_thread = None
        
        # Initialize MQTT client
        self.client = mqtt.Client(client_id=self.client_id)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        
        self.logger.info(f"MQTT client initialized: {self.broker_host}:{self.broker_port}")
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker"""
        if rc == 0:
            self.connected = True
            self.logger.info("Connected to MQTT broker successfully")
            
            # Subscribe to topics
            self.client.subscribe(self.topics['weight_data'], qos=self.qos)
            self.client.subscribe(self.topics['status'], qos=self.qos)
            
            self.logger.info(f"Subscribed to topics: {self.topics}")
        else:
            self.connected = False
            self.logger.error(f"MQTT connection failed with code: {rc}")
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from MQTT broker"""
        self.connected = False
        self.logger.warning(f"Disconnected from MQTT broker (code: {rc})")
        
        # Start reconnection thread if not already running
        if not self.reconnect_thread or not self.reconnect_thread.is_alive():
            self.reconnect_thread = Thread(target=self._reconnect_loop, daemon=True)
            self.reconnect_thread.start()
    
    def _reconnect_loop(self):
        """Attempt to reconnect to MQTT broker"""
        retry_delay = 5
        max_delay = 60
        
        while not self.connected and not self.reconnect_event.is_set():
            try:
                self.logger.info(f"Attempting to reconnect to MQTT broker...")
                self.client.reconnect()
                break
            except Exception as e:
                self.logger.warning(f"Reconnection failed: {e}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)
                
    def _on_message(self, client, userdata, msg):
        """Callback when message received"""
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            
            self.logger.debug(f"MQTT message received: {topic}")
            
            # Parse JSON payload
            data = json.loads(payload)
            
            # Route message based on topic
            if 'weight' in topic:
                if self.weight_callback:
                    self.weight_callback(data)
            elif 'status' in topic:
                if self.status_callback:
                    self.status_callback(data)
                    
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse MQTT message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing MQTT message: {e}")
    
    def connect(self):
        """Connect to MQTT broker"""
        try:
            self.client.connect(
                self.broker_host,
                self.broker_port,
                self.keepalive
            )
            self.client.loop_start()
            
            # Wait for connection (with timeout)
            timeout = 10
            start_time = time.time()
            while not self.connected and (time.time() - start_time) < timeout:
                time.sleep(0.1)
            
            if not self.connected:
                self.logger.warning("MQTT connection timeout")
            
        except Exception as e:
            self.logger.error(f"Failed to connect to MQTT broker: {e}")
            raise
    
    def disconnect(self):
        """Disconnect from MQTT broker"""
        self.reconnect_event.set()  # Stop reconnection attempts
        self.client.loop_stop()
        self.client.disconnect()
        self.connected = False
        self.logger.info("Disconnected from MQTT broker")
    
    def publish(self, topic: str, payload: Dict[str, Any], retain: bool = False):
        """
        Publish message to MQTT topic
        
        Args:
            topic: MQTT topic
            payload: Message payload (will be JSON-encoded)
            retain: Whether to retain message
        """
        try:
            message = json.dumps(payload)
            result = self.client.publish(topic, message, qos=self.qos, retain=retain)
            
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                self.logger.debug(f"Published to {topic}: {payload}")
            else:
                self.logger.error(f"Failed to publish to {topic}: {result.rc}")
                
        except Exception as e:
            self.logger.error(f"Error publishing MQTT message: {e}")
            
    def send_command(self, station_id: str, command: str, params: Dict[str, Any] = None):
        """
        Send command to M5StickC station
        
        Args:
            station_id: Target station ID
            command: Command name (e.g., 'tare', 'calibrate')
            params: Command parameters
        """
        topic = f"{self.topics['commands']}/{station_id}"
        payload = {
            'command': command,
            'params': params or {},
            'timestamp': time.time()
        }
        self.publish(topic, payload)
        self.logger.info(f"Sent command to {station_id}: {command}")
    
    def tare_sensor(self, station_id: str):
        """Send tare command to weight sensor"""
        self.send_command(station_id, 'tare')
    
    def calibrate_sensor(self, station_id: str, known_weight_g: float):
        """
        Send calibration command to weight sensor
        
        Args:
            station_id: Station ID
            known_weight_g: Known weight in grams for calibration
        """
        self.send_command(station_id, 'calibrate', {'weight_g': known_weight_g})
    
    def set_weight_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """
        Set callback for weight data
        
        Args:
            callback: Function to call when weight data received
        """
        self.weight_callback = callback
        self.logger.info("Weight data callback registered")

    def set_status_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """
        Set callback for status updates
        
        Args:
            callback: Function to call when status update received
        """
        self.status_callback = callback
        self.logger.info("Status callback registered")
    
    def is_connected(self) -> bool:
        """Check if connected to MQTT broker"""
        return self.connected
