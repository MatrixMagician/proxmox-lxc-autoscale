"""Advanced error recovery mechanisms with retry strategies and fallback handling."""

import asyncio
import logging
import time
import random
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union, Tuple
from enum import Enum
from dataclasses import dataclass
from functools import wraps
import traceback
from concurrent.futures import ThreadPoolExecutor

from circuit_breaker import circuit_breaker, CircuitBreakerError

T = TypeVar('T')


class RetryStrategy(Enum):
    """Retry strategy types."""
    FIXED_DELAY = "fixed_delay"
    EXPONENTIAL_BACKOFF = "exponential_backoff"
    LINEAR_BACKOFF = "linear_backoff"
    FIBONACCI_BACKOFF = "fibonacci_backoff"
    RANDOM_JITTER = "random_jitter"


@dataclass
class RetryConfig:
    """Configuration for retry mechanism."""
    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL_BACKOFF
    jitter: bool = True
    backoff_multiplier: float = 2.0
    retryable_exceptions: tuple = (Exception,)
    non_retryable_exceptions: tuple = (ValueError, TypeError)


@dataclass
class FallbackConfig:
    """Configuration for fallback mechanisms."""
    enable_graceful_degradation: bool = True
    enable_cache_fallback: bool = True
    enable_default_values: bool = True
    enable_notification: bool = True
    fallback_timeout: float = 5.0


class ErrorRecoveryManager:
    """Advanced error recovery manager with multiple strategies."""
    
    def __init__(self):
        """Initialize error recovery manager."""
        self.recovery_stats = {
            'total_operations': 0,
            'successful_operations': 0,
            'failed_operations': 0,
            'retries_attempted': 0,
            'fallbacks_used': 0,
            'circuit_breaker_activations': 0,
            'avg_retry_count': 0.0
        }
        self._fallback_cache: Dict[str, Any] = {}
        self._thread_pool = ThreadPoolExecutor(max_workers=4)
    
    def calculate_delay(self, attempt: int, config: RetryConfig) -> float:
        """Calculate delay for retry attempt based on strategy.
        
        Args:
            attempt: Current attempt number (0-based)
            config: Retry configuration
            
        Returns:
            Delay in seconds
        """
        if config.strategy == RetryStrategy.FIXED_DELAY:
            delay = config.base_delay
        elif config.strategy == RetryStrategy.EXPONENTIAL_BACKOFF:
            delay = config.base_delay * (config.backoff_multiplier ** attempt)
        elif config.strategy == RetryStrategy.LINEAR_BACKOFF:
            delay = config.base_delay * (attempt + 1)
        elif config.strategy == RetryStrategy.FIBONACCI_BACKOFF:
            delay = config.base_delay * self._fibonacci(attempt + 1)
        elif config.strategy == RetryStrategy.RANDOM_JITTER:
            delay = config.base_delay + random.uniform(0, config.base_delay)
        else:
            delay = config.base_delay
        
        # Apply jitter if enabled
        if config.jitter and config.strategy != RetryStrategy.RANDOM_JITTER:
            jitter_amount = delay * 0.1  # 10% jitter
            delay += random.uniform(-jitter_amount, jitter_amount)
        
        # Cap the delay
        return min(delay, config.max_delay)
    
    def _fibonacci(self, n: int) -> int:
        """Calculate fibonacci number."""
        if n <= 1:
            return n
        a, b = 0, 1
        for _ in range(2, n + 1):
            a, b = b, a + b
        return b
    
    def _is_retryable_exception(self, exception: Exception, config: RetryConfig) -> bool:
        """Check if exception is retryable.
        
        Args:
            exception: Exception to check
            config: Retry configuration
            
        Returns:
            True if exception is retryable
        """
        # Check non-retryable exceptions first
        if isinstance(exception, config.non_retryable_exceptions):
            return False
        
        # Check retryable exceptions
        return isinstance(exception, config.retryable_exceptions)
    
    async def execute_with_retry(
        self,
        func: Callable[..., T],
        *args,
        retry_config: RetryConfig = None,
        fallback_config: FallbackConfig = None,
        operation_name: str = "unknown",
        **kwargs
    ) -> T:
        """Execute function with comprehensive error recovery.
        
        Args:
            func: Function to execute
            *args: Function arguments
            retry_config: Retry configuration
            fallback_config: Fallback configuration
            operation_name: Name for logging and caching
            **kwargs: Function keyword arguments
            
        Returns:
            Function result
            
        Raises:
            Exception: If all recovery mechanisms fail
        """
        retry_config = retry_config or RetryConfig()
        fallback_config = fallback_config or FallbackConfig()
        
        self.recovery_stats['total_operations'] += 1
        
        last_exception = None
        retry_count = 0
        
        for attempt in range(retry_config.max_attempts):
            try:
                # Execute the function
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)
                
                # Success - update cache and stats
                self._update_fallback_cache(operation_name, result)
                self.recovery_stats['successful_operations'] += 1
                
                # Update retry statistics
                if retry_count > 0:
                    total_ops = self.recovery_stats['total_operations']
                    current_avg = self.recovery_stats['avg_retry_count']
                    self.recovery_stats['avg_retry_count'] = (
                        (current_avg * (total_ops - 1) + retry_count) / total_ops
                    )
                
                return result
                
            except CircuitBreakerError:
                # Circuit breaker is open - try fallback immediately
                logging.warning(f"Circuit breaker open for {operation_name}, attempting fallback")
                self.recovery_stats['circuit_breaker_activations'] += 1
                return await self._execute_fallback(operation_name, fallback_config, last_exception)
                
            except Exception as e:
                last_exception = e
                
                # Check if this is the last attempt
                if attempt == retry_config.max_attempts - 1:
                    break
                
                # Check if exception is retryable
                if not self._is_retryable_exception(e, retry_config):
                    logging.error(f"Non-retryable exception in {operation_name}: {e}")
                    break
                
                # Calculate delay and wait
                delay = self.calculate_delay(attempt, retry_config)
                retry_count += 1
                self.recovery_stats['retries_attempted'] += 1
                
                logging.warning(
                    f"Attempt {attempt + 1}/{retry_config.max_attempts} failed for {operation_name}: {e}. "
                    f"Retrying in {delay:.2f}s"
                )
                
                await asyncio.sleep(delay)
        
        # All retries failed - try fallback mechanisms
        logging.error(f"All retries failed for {operation_name}, attempting fallback")
        self.recovery_stats['failed_operations'] += 1
        
        return await self._execute_fallback(operation_name, fallback_config, last_exception)
    
    async def _execute_fallback(
        self,
        operation_name: str,
        config: FallbackConfig,
        last_exception: Exception
    ) -> Any:
        """Execute fallback mechanisms.
        
        Args:
            operation_name: Name of the operation
            config: Fallback configuration
            last_exception: Last exception that occurred
            
        Returns:
            Fallback result
            
        Raises:
            Exception: If all fallback mechanisms fail
        """
        self.recovery_stats['fallbacks_used'] += 1
        
        # Try cache fallback first
        if config.enable_cache_fallback:
            cached_result = self._get_cached_result(operation_name)
            if cached_result is not None:
                logging.info(f"Using cached fallback for {operation_name}")
                return cached_result
        
        # Try graceful degradation
        if config.enable_graceful_degradation:
            degraded_result = await self._attempt_graceful_degradation(
                operation_name, config.fallback_timeout
            )
            if degraded_result is not None:
                logging.info(f"Using graceful degradation for {operation_name}")
                return degraded_result
        
        # Try default values
        if config.enable_default_values:
            default_result = self._get_default_value(operation_name)
            if default_result is not None:
                logging.info(f"Using default value for {operation_name}")
                return default_result
        
        # Send notification if enabled
        if config.enable_notification:
            await self._send_failure_notification(operation_name, last_exception)
        
        # All fallback mechanisms failed
        logging.error(f"All fallback mechanisms failed for {operation_name}")
        raise last_exception
    
    def _update_fallback_cache(self, operation_name: str, result: Any) -> None:
        """Update fallback cache with successful result.
        
        Args:
            operation_name: Operation name
            result: Result to cache
        """
        self._fallback_cache[operation_name] = {
            'result': result,
            'timestamp': time.time()
        }
    
    def _get_cached_result(self, operation_name: str, max_age: float = 300.0) -> Any:
        """Get cached result if available and not too old.
        
        Args:
            operation_name: Operation name
            max_age: Maximum age of cached result in seconds
            
        Returns:
            Cached result or None
        """
        if operation_name in self._fallback_cache:
            cache_entry = self._fallback_cache[operation_name]
            age = time.time() - cache_entry['timestamp']
            
            if age <= max_age:
                return cache_entry['result']
            else:
                # Remove stale cache entry
                del self._fallback_cache[operation_name]
        
        return None
    
    async def _attempt_graceful_degradation(
        self,
        operation_name: str,
        timeout: float
    ) -> Any:
        """Attempt graceful degradation for known operations.
        
        Args:
            operation_name: Operation name
            timeout: Timeout for degraded operation
            
        Returns:
            Degraded result or None
        """
        try:
            # Define graceful degradation strategies for different operations
            degradation_strategies = {
                'get_container_list': self._get_minimal_container_list,
                'get_system_resources': self._get_minimal_system_resources,
                'execute_scaling_command': self._execute_minimal_scaling,
            }
            
            if operation_name in degradation_strategies:
                strategy = degradation_strategies[operation_name]
                return await asyncio.wait_for(strategy(), timeout=timeout)
            
        except Exception as e:
            logging.error(f"Graceful degradation failed for {operation_name}: {e}")
        
        return None
    
    async def _get_minimal_container_list(self) -> List[str]:
        """Get minimal container list as fallback."""
        # Return a basic container list based on configuration
        try:
            from config_manager import config_manager
            all_containers = []
            
            # Get containers from all tier configurations
            for tier_name in config_manager.get_all_tier_names():
                tier_config = config_manager.get_tier_config_by_name(tier_name)
                containers = tier_config.get('lxc_containers', [])
                all_containers.extend(str(c) for c in containers)
            
            return list(set(all_containers))  # Remove duplicates
            
        except Exception:
            return []
    
    async def _get_minimal_system_resources(self) -> Dict[str, int]:
        """Get minimal system resources as fallback."""
        # Return conservative resource estimates
        return {
            'total_cores': 4,  # Conservative estimate
            'total_memory': 8192,  # 8GB conservative estimate
            'available_cores': 2,
            'available_memory': 4096
        }
    
    async def _execute_minimal_scaling(self) -> Dict[str, Any]:
        """Execute minimal scaling operation as fallback."""
        # Return a no-op scaling result
        return {
            'success': False,
            'reason': 'Fallback mode - no scaling performed',
            'containers_affected': 0
        }
    
    def _get_default_value(self, operation_name: str) -> Any:
        """Get default value for operation.
        
        Args:
            operation_name: Operation name
            
        Returns:
            Default value or None
        """
        defaults = {
            'get_cpu_usage': 0.0,
            'get_memory_usage': 0.0,
            'get_container_count': 0,
            'scaling_result': {'success': False, 'reason': 'Default value used'},
        }
        
        return defaults.get(operation_name)
    
    async def _send_failure_notification(self, operation_name: str, exception: Exception) -> None:
        """Send notification about operation failure.
        
        Args:
            operation_name: Operation name
            exception: Exception that occurred
        """
        try:
            from notification import send_notification
            
            message = (
                f"Operation '{operation_name}' failed after all recovery attempts.\n"
                f"Error: {str(exception)}\n"
                f"All fallback mechanisms exhausted."
            )
            
            # Send notification in thread pool to avoid blocking
            await asyncio.get_event_loop().run_in_executor(
                self._thread_pool,
                lambda: send_notification(
                    f"Critical Failure: {operation_name}",
                    message,
                    priority=9
                )
            )
            
        except Exception as e:
            logging.error(f"Failed to send failure notification: {e}")
    
    def get_recovery_stats(self) -> Dict[str, Any]:
        """Get error recovery statistics."""
        stats = self.recovery_stats.copy()
        
        if stats['total_operations'] > 0:
            stats['success_rate'] = (stats['successful_operations'] / stats['total_operations']) * 100
            stats['failure_rate'] = (stats['failed_operations'] / stats['total_operations']) * 100
            stats['fallback_rate'] = (stats['fallbacks_used'] / stats['total_operations']) * 100
        else:
            stats['success_rate'] = 0.0
            stats['failure_rate'] = 0.0
            stats['fallback_rate'] = 0.0
        
        stats['cache_entries'] = len(self._fallback_cache)
        return stats
    
    def clear_cache(self) -> None:
        """Clear fallback cache."""
        self._fallback_cache.clear()
        logging.info("Fallback cache cleared")
    
    async def cleanup(self) -> None:
        """Cleanup error recovery manager resources."""
        self._thread_pool.shutdown(wait=True)
        self.clear_cache()
        logging.info("Error recovery manager cleaned up")


# Global error recovery manager
_global_recovery_manager = ErrorRecoveryManager()


def get_error_recovery_manager() -> ErrorRecoveryManager:
    """Get the global error recovery manager."""
    return _global_recovery_manager


def robust_operation(
    retry_attempts: int = 3,
    base_delay: float = 1.0,
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL_BACKOFF,
    enable_fallback: bool = True,
    operation_name: str = None
):
    """Decorator for making operations robust with error recovery.
    
    Args:
        retry_attempts: Maximum retry attempts
        base_delay: Base delay between retries
        strategy: Retry strategy to use
        enable_fallback: Enable fallback mechanisms
        operation_name: Name for the operation (defaults to function name)
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        op_name = operation_name or func.__name__
        
        retry_config = RetryConfig(
            max_attempts=retry_attempts,
            base_delay=base_delay,
            strategy=strategy
        )
        
        fallback_config = FallbackConfig(
            enable_graceful_degradation=enable_fallback,
            enable_cache_fallback=enable_fallback,
            enable_default_values=enable_fallback
        )
        
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs) -> T:
                return await _global_recovery_manager.execute_with_retry(
                    func, *args,
                    retry_config=retry_config,
                    fallback_config=fallback_config,
                    operation_name=op_name,
                    **kwargs
                )
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs) -> T:
                # For sync functions, run in async context
                async def async_func(*a, **kw):
                    return func(*a, **kw)
                
                return asyncio.run(
                    _global_recovery_manager.execute_with_retry(
                        async_func, *args,
                        retry_config=retry_config,
                        fallback_config=fallback_config,
                        operation_name=op_name,
                        **kwargs
                    )
                )
            return sync_wrapper
    
    return decorator