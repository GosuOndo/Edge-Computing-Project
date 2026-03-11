"""
Smart Medication System - State Machine

Manages system operational states and state transitions.
"""

from enum import Enum, auto
from typing import Callable, Dict, Any
from threading import Lock


class SystemState(Enum):
    """System operational states"""
    IDLE = auto()                    # No active medication reminders
    REMINDER_ACTIVE = auto()         # Reminder displayed, waiting for user action
    WAITING_FOR_INTAKE = auto()      # Weight change detected, waiting for confirmation
    MONITORING_PATIENT = auto()      # Monitoring patient behavior (30s window)
    VERIFYING = auto()               # Verifying dosage and OCR
    ALERTING = auto()                # Sending alerts/notifications
    ERROR = auto()                   # System error state
    SETUP = auto()                   # Setup/configuration mode


class StateMachine:
    """State machine for medication system"""
    
    def __init__(self, logger):
        """
        Initialize state machine
        
        Args:
            logger: Logger instance
        """
        self.logger = logger
        self.current_state = SystemState.IDLE
        self.previous_state = None
        self.state_data = {}  # Data associated with current state
        self.state_lock = Lock()
        
        # State transition callbacks
        self.state_callbacks = {}
        
        self.logger.info(f"State machine initialized in {self.current_state.name} state")
    
    def transition_to(self, new_state: SystemState, data: Dict[str, Any] = None):
        """
        Transition to a new state
        
        Args:
            new_state: Target state
            data: State-specific data
        """
        with self.state_lock:
            if new_state == self.current_state:
                self.logger.debug(f"Already in {new_state.name} state")
                return
            
            self.previous_state = self.current_state
            self.current_state = new_state
            self.state_data = data or {}
            
            self.logger.info(
                f"State transition: {self.previous_state.name} -> {self.current_state.name}"
            )
            
            # Call state-specific callback if registered
            if new_state in self.state_callbacks:
                try:
                    self.state_callbacks[new_state](data)
                except Exception as e:
                    self.logger.error(f"Error in state callback: {e}")
    
    def get_state(self) -> SystemState:
        """Get current state"""
        return self.current_state
    
    def get_state_name(self) -> str:
        """Get current state name as string"""
        return self.current_state.name
    
    def get_state_data(self) -> Dict[str, Any]:
        """Get data associated with current state"""
        return self.state_data.copy()
    
    def update_state_data(self, key: str, value: Any):
        """
        Update state data
        
        Args:
            key: Data key
            value: Data value
        """
        with self.state_lock:
            self.state_data[key] = value
            
    def is_idle(self) -> bool:
        """Check if system is idle"""
        return self.current_state == SystemState.IDLE
    
    def is_busy(self) -> bool:
        """Check if system is busy (not idle)"""
        return self.current_state != SystemState.IDLE
    
    def register_state_callback(self, state: SystemState, callback: Callable[[Dict[str, Any]], None]):
        """
        Register callback for state entry
        
        Args:
            state: State to register callback for
            callback: Function to call when entering state
        """
        self.state_callbacks[state] = callback
        self.logger.debug(f"Registered callback for {state.name} state")
    
    def reset_to_idle(self):
        """Reset state machine to IDLE"""
        self.transition_to(SystemState.IDLE, {})
    
    def can_transition_to(self, target_state: SystemState) -> bool:
        """
        Check if transition to target state is valid
        
        Args:
            target_state: Target state
            
        Returns:
            True if transition is valid
        """
        # Define valid transitions
        valid_transitions = {
            SystemState.IDLE: [
                SystemState.REMINDER_ACTIVE,
                SystemState.SETUP,
                SystemState.ERROR
            ],
            SystemState.REMINDER_ACTIVE: [
                SystemState.WAITING_FOR_INTAKE,
                SystemState.IDLE,
                SystemState.ALERTING,
                SystemState.ERROR
            ],
            SystemState.WAITING_FOR_INTAKE: [
                SystemState.VERIFYING,
                SystemState.IDLE,
                SystemState.ERROR
            ],
            SystemState.VERIFYING: [
                SystemState.MONITORING_PATIENT,
                SystemState.ALERTING,
                SystemState.IDLE,
                SystemState.ERROR
            ],
            SystemState.MONITORING_PATIENT: [
                SystemState.ALERTING,
                SystemState.IDLE,
                SystemState.ERROR
            ],
            SystemState.ALERTING: [
                SystemState.IDLE,
                SystemState.ERROR
            ],
            SystemState.ERROR: [
                SystemState.IDLE
            ],
            SystemState.SETUP: [
                SystemState.IDLE,
                SystemState.ERROR
            ]
        }
        
        allowed = valid_transitions.get(self.current_state, [])
        return target_state in allowed
