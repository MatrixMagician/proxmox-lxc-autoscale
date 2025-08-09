"""Centralized error handling for the LXC autoscaling system."""

import logging
import time
from functools import wraps
from typing import Any, Callable, Optional, Type, TypeVar, Union

F = TypeVar('F', bound=Callable[..., Any])


class LXCAutoscaleError(Exception):
    """Base exception for LXC autoscaling errors."""
    pass


class ConfigurationError(LXCAutoscaleError):
    """Raised when there are configuration issues."""
    pass


class ContainerError(LXCAutoscaleError):
    """Raised when container operations fail."""
    pass


class ScalingError(LXCAutoscaleError):
    """Raised when scaling operations fail."""
    pass


class ValidationError(LXCAutoscaleError):
    """Raised when validation fails."""
    pass


def retry_on_failure(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: tuple = (Exception,)
) -> Callable[[F], F]:
    """Decorator to retry function calls on failure.
    
    Args:
        max_retries: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff_factor: Factor by which delay increases after each retry
        exceptions: Tuple of exceptions to catch and retry on
    
    Returns:
        Decorated function with retry logic
    """
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        logging.warning(
                            f"Attempt {attempt + 1}/{max_retries + 1} failed for {func.__name__}: {e}. "
                            f"Retrying in {current_delay:.1f}s..."
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff_factor
                    else:
                        logging.error(
                            f"All {max_retries + 1} attempts failed for {func.__name__}: {e}"
                        )
            
            raise last_exception
        return wrapper
    return decorator



def handle_container_errors(func: F) -> F:
    """Decorator to handle container-related errors."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (ValueError, KeyError) as e:
            logging.error(f"Container data error in {func.__name__}: {e}")
            raise ContainerError(f"Container operation failed: {e}") from e
    return wrapper


def handle_configuration_errors(func: F) -> F:
    """Decorator to handle configuration-related errors."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except (KeyError, ValueError, TypeError) as e:
            logging.error(f"Configuration error in {func.__name__}: {e}")
            raise ConfigurationError(f"Configuration validation failed: {e}") from e
    return wrapper


def safe_execute(
    func: Callable,
    *args,
    default: Any = None,
    log_errors: bool = True,
    **kwargs
) -> Any:
    """Safely execute a function with error handling.
    
    Args:
        func: Function to execute
        *args: Positional arguments for the function
        default: Default value to return on error
        log_errors: Whether to log errors
        **kwargs: Keyword arguments for the function
    
    Returns:
        Function result or default value on error
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        if log_errors:
            logging.error(f"Error executing {func.__name__}: {e}")
        return default


class ErrorHandler:
    """Centralized error handler for the application."""
    
    @staticmethod
    def handle_critical_error(error: Exception, context: str = "") -> None:
        """Handle critical errors that should stop execution.
        
        Args:
            error: The exception that occurred
            context: Additional context about where the error occurred
        """
        error_msg = f"Critical error{' in ' + context if context else ''}: {error}"
        logging.critical(error_msg)
        raise SystemExit(1) from error
    
    @staticmethod
    def handle_recoverable_error(error: Exception, context: str = "") -> None:
        """Handle recoverable errors that should be logged but not stop execution.
        
        Args:
            error: The exception that occurred
            context: Additional context about where the error occurred
        """
        error_msg = f"Recoverable error{' in ' + context if context else ''}: {error}"
        logging.error(error_msg)
    
    @staticmethod
    def validate_required_config(config: dict, required_keys: list, section: str = "") -> None:
        """Validate that required configuration keys are present.
        
        Args:
            config: Configuration dictionary to validate
            required_keys: List of required keys
            section: Configuration section name for error messages
        
        Raises:
            ConfigurationError: If required keys are missing
        """
        missing_keys = [key for key in required_keys if key not in config]
        if missing_keys:
            section_str = f" in section '{section}'" if section else ""
            raise ConfigurationError(
                f"Missing required configuration keys{section_str}: {', '.join(missing_keys)}"
            )
    
    @staticmethod
    def validate_threshold_ranges(
        lower: Union[int, float],
        upper: Union[int, float],
        resource_name: str = "resource"
    ) -> None:
        """Validate that threshold values are in correct ranges.
        
        Args:
            lower: Lower threshold value
            upper: Upper threshold value
            resource_name: Name of the resource for error messages
        
        Raises:
            ValidationError: If thresholds are invalid
        """
        if not (0 <= lower < upper <= 100):
            raise ValidationError(
                f"Invalid {resource_name} thresholds: lower={lower}, upper={upper}. "
                "Lower must be less than upper, and both must be between 0 and 100."
            )