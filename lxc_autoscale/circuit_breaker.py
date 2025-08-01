"""Circuit breaker pattern implementation for enhanced reliability."""

import asyncio
import time
import logging
from typing import Any, Callable, Dict, Optional, TypeVar, Union
from enum import Enum
from dataclasses import dataclass, field
from functools import wraps
import threading
from concurrent.futures import Future

T = TypeVar('T')


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, requests blocked
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5          # Number of failures before opening
    recovery_timeout: float = 60.0      # Seconds before trying half-open
    success_threshold: int = 3          # Successes needed to close from half-open
    timeout: float = 30.0               # Operation timeout
    expected_exceptions: tuple = (Exception,)  # Exceptions that count as failures


@dataclass
class CircuitBreakerStats:
    """Statistics for circuit breaker monitoring."""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    state_changes: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    
    def success_rate(self) -> float:
        """Calculate success rate percentage."""
        if self.total_requests == 0:
            return 0.0
        return (self.successful_requests / self.total_requests) * 100


class CircuitBreakerError(Exception):
    """Exception raised when circuit breaker is open."""
    pass


class CircuitBreaker:
    """High-performance circuit breaker with async support."""
    
    def __init__(self, name: str, config: CircuitBreakerConfig = None):
        """Initialize circuit breaker.
        
        Args:
            name: Circuit breaker name for logging
            config: Circuit breaker configuration
        """
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.stats = CircuitBreakerStats()
        self._lock = threading.RLock()
        self._state_change_time = time.time()
        
        # Event handlers
        self._on_state_change: Optional[Callable] = None
        self._on_failure: Optional[Callable] = None
        self._on_success: Optional[Callable] = None
    
    def set_event_handlers(
        self,
        on_state_change: Optional[Callable] = None,
        on_failure: Optional[Callable] = None,
        on_success: Optional[Callable] = None
    ) -> None:
        """Set event handlers for circuit breaker events.
        
        Args:
            on_state_change: Called when state changes
            on_failure: Called on operation failure
            on_success: Called on operation success
        """
        self._on_state_change = on_state_change
        self._on_failure = on_failure
        self._on_success = on_success
    
    def _change_state(self, new_state: CircuitState) -> None:
        """Change circuit breaker state.
        
        Args:
            new_state: New state to transition to
        """
        if self.state != new_state:
            old_state = self.state
            self.state = new_state
            self._state_change_time = time.time()
            self.stats.state_changes += 1
            
            logging.info(f"Circuit breaker '{self.name}' state changed: {old_state.value} -> {new_state.value}")
            
            # Call event handler if set
            if self._on_state_change:
                try:
                    self._on_state_change(self.name, old_state, new_state)
                except Exception as e:
                    logging.error(f"Error in state change handler for '{self.name}': {e}")
    
    def _should_attempt_reset(self) -> bool:
        """Check if circuit should attempt reset from OPEN to HALF_OPEN."""
        return (
            self.state == CircuitState.OPEN and
            time.time() - self._state_change_time >= self.config.recovery_timeout
        )
    
    def _record_success(self) -> None:
        """Record a successful operation."""
        with self._lock:
            self.stats.total_requests += 1
            self.stats.successful_requests += 1
            self.stats.consecutive_successes += 1
            self.stats.consecutive_failures = 0
            self.stats.last_success_time = time.time()
            
            if self.state == CircuitState.HALF_OPEN:
                if self.stats.consecutive_successes >= self.config.success_threshold:
                    self._change_state(CircuitState.CLOSED)
            
            # Call success handler if set
            if self._on_success:
                try:
                    self._on_success(self.name)
                except Exception as e:
                    logging.error(f"Error in success handler for '{self.name}': {e}")
    
    def _record_failure(self, exception: Exception) -> None:
        """Record a failed operation.
        
        Args:
            exception: The exception that caused the failure
        """
        with self._lock:
            self.stats.total_requests += 1
            self.stats.failed_requests += 1
            self.stats.consecutive_failures += 1
            self.stats.consecutive_successes = 0
            self.stats.last_failure_time = time.time()
            
            if self.state == CircuitState.CLOSED:
                if self.stats.consecutive_failures >= self.config.failure_threshold:
                    self._change_state(CircuitState.OPEN)
            elif self.state == CircuitState.HALF_OPEN:
                self._change_state(CircuitState.OPEN)
            
            # Call failure handler if set
            if self._on_failure:
                try:
                    self._on_failure(self.name, exception)
                except Exception as e:
                    logging.error(f"Error in failure handler for '{self.name}': {e}")
    
    def _can_execute(self) -> bool:
        """Check if operation can be executed."""
        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            elif self.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._change_state(CircuitState.HALF_OPEN)
                    return True
                return False
            elif self.state == CircuitState.HALF_OPEN:
                return True
            return False
    
    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        """Decorator for applying circuit breaker to a function."""
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs) -> T:
                return await self.call_async(func, *args, **kwargs)
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs) -> T:
                return self.call(func, *args, **kwargs)
            return sync_wrapper
    
    def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """Execute function with circuit breaker protection.
        
        Args:
            func: Function to execute
            *args: Function arguments
            **kwargs: Function keyword arguments
            
        Returns:
            Function result
            
        Raises:
            CircuitBreakerError: If circuit is open
        """
        if not self._can_execute():
            raise CircuitBreakerError(f"Circuit breaker '{self.name}' is OPEN")
        
        try:
            result = func(*args, **kwargs)
            self._record_success()
            return result
        except self.config.expected_exceptions as e:
            self._record_failure(e)
            raise
    
    async def call_async(self, func: Callable[..., T], *args, **kwargs) -> T:
        """Execute async function with circuit breaker protection.
        
        Args:
            func: Async function to execute
            *args: Function arguments
            **kwargs: Function keyword arguments
            
        Returns:
            Function result
            
        Raises:
            CircuitBreakerError: If circuit is open
        """
        if not self._can_execute():
            raise CircuitBreakerError(f"Circuit breaker '{self.name}' is OPEN")
        
        try:
            result = await asyncio.wait_for(
                func(*args, **kwargs),
                timeout=self.config.timeout
            )
            self._record_success()
            return result
        except (asyncio.TimeoutError, *self.config.expected_exceptions) as e:
            self._record_failure(e)
            raise
    
    def get_state(self) -> CircuitState:
        """Get current circuit breaker state."""
        return self.state
    
    def get_stats(self) -> CircuitBreakerStats:
        """Get circuit breaker statistics."""
        with self._lock:
            return CircuitBreakerStats(
                total_requests=self.stats.total_requests,
                successful_requests=self.stats.successful_requests,
                failed_requests=self.stats.failed_requests,
                state_changes=self.stats.state_changes,
                last_failure_time=self.stats.last_failure_time,
                last_success_time=self.stats.last_success_time,
                consecutive_failures=self.stats.consecutive_failures,
                consecutive_successes=self.stats.consecutive_successes
            )
    
    def reset(self) -> None:
        """Manually reset circuit breaker to CLOSED state."""
        with self._lock:
            self._change_state(CircuitState.CLOSED)
            self.stats.consecutive_failures = 0
            self.stats.consecutive_successes = 0
            logging.info(f"Circuit breaker '{self.name}' manually reset")
    
    def force_open(self) -> None:
        """Manually force circuit breaker to OPEN state."""
        with self._lock:
            self._change_state(CircuitState.OPEN)
            logging.info(f"Circuit breaker '{self.name}' manually opened")


class CircuitBreakerManager:
    """Manager for multiple circuit breakers."""
    
    def __init__(self):
        """Initialize circuit breaker manager."""
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = threading.RLock()
    
    def get_breaker(self, name: str, config: CircuitBreakerConfig = None) -> CircuitBreaker:
        """Get or create a circuit breaker.
        
        Args:
            name: Circuit breaker name
            config: Configuration (uses default if None)
            
        Returns:
            Circuit breaker instance
        """
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name, config)
                logging.info(f"Created circuit breaker: {name}")
            return self._breakers[name]
    
    def get_all_stats(self) -> Dict[str, CircuitBreakerStats]:
        """Get statistics for all circuit breakers.
        
        Returns:
            Dictionary mapping breaker names to statistics
        """
        with self._lock:
            return {name: breaker.get_stats() for name, breaker in self._breakers.items()}
    
    def reset_all(self) -> None:
        """Reset all circuit breakers."""
        with self._lock:
            for breaker in self._breakers.values():
                breaker.reset()
            logging.info("All circuit breakers reset")
    
    def get_unhealthy_breakers(self) -> Dict[str, CircuitBreaker]:
        """Get circuit breakers that are not in CLOSED state.
        
        Returns:
            Dictionary of unhealthy circuit breakers
        """
        with self._lock:
            return {
                name: breaker for name, breaker in self._breakers.items()
                if breaker.get_state() != CircuitState.CLOSED
            }


# Global circuit breaker manager instance
_global_breaker_manager = CircuitBreakerManager()


def get_circuit_breaker_manager() -> CircuitBreakerManager:
    """Get the global circuit breaker manager."""
    return _global_breaker_manager


def circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 60.0,
    success_threshold: int = 3,
    timeout: float = 30.0,
    expected_exceptions: tuple = (Exception,)
) -> Callable:
    """Decorator for applying circuit breaker protection.
    
    Args:
        name: Circuit breaker name
        failure_threshold: Failures before opening
        recovery_timeout: Seconds before attempting recovery
        success_threshold: Successes needed to close
        timeout: Operation timeout
        expected_exceptions: Exceptions that trigger circuit breaker
        
    Returns:
        Decorator function
    """
    config = CircuitBreakerConfig(
        failure_threshold=failure_threshold,
        recovery_timeout=recovery_timeout,
        success_threshold=success_threshold,
        timeout=timeout,
        expected_exceptions=expected_exceptions
    )
    
    breaker = _global_breaker_manager.get_breaker(name, config)
    return breaker