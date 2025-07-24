"""Structured logging utilities for the LXC autoscaling system."""

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Union

from config_manager import config_manager


class StructuredLogger:
    """Provides structured logging capabilities with JSON formatting."""
    
    def __init__(self, logger_name: str = __name__):
        """Initialize structured logger.
        
        Args:
            logger_name: Name of the logger instance
        """
        self.logger = logging.getLogger(logger_name)
        self.hostname = config_manager.get_proxmox_hostname()
    
    def _create_log_entry(
        self,
        level: str,
        message: str,
        container_id: Optional[str] = None,
        event_type: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Create a structured log entry.
        
        Args:
            level: Log level (info, warning, error, etc.)
            message: Log message
            container_id: Optional container ID
            event_type: Optional event type
            **kwargs: Additional fields to include
            
        Returns:
            Dictionary containing structured log data
        """
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'hostname': self.hostname,
            'level': level.upper(),
            'message': message,
            'service': 'lxc_autoscale'
        }
        
        if container_id:
            log_entry['container_id'] = container_id
            
        if event_type:
            log_entry['event_type'] = event_type
        
        # Add any additional fields
        log_entry.update(kwargs)
        
        return log_entry
    
    def info(
        self,
        message: str,
        container_id: Optional[str] = None,
        event_type: Optional[str] = None,
        **kwargs
    ) -> None:
        """Log info level message with structured data.
        
        Args:
            message: Log message
            container_id: Optional container ID
            event_type: Optional event type
            **kwargs: Additional fields
        """
        log_entry = self._create_log_entry('info', message, container_id, event_type, **kwargs)
        self.logger.info(json.dumps(log_entry))
    
    def warning(
        self,
        message: str,
        container_id: Optional[str] = None,
        event_type: Optional[str] = None,
        **kwargs
    ) -> None:
        """Log warning level message with structured data.
        
        Args:
            message: Log message
            container_id: Optional container ID
            event_type: Optional event type
            **kwargs: Additional fields
        """
        log_entry = self._create_log_entry('warning', message, container_id, event_type, **kwargs)
        self.logger.warning(json.dumps(log_entry))
    
    def error(
        self,
        message: str,
        container_id: Optional[str] = None,
        event_type: Optional[str] = None,
        error: Optional[Exception] = None,
        **kwargs
    ) -> None:
        """Log error level message with structured data.
        
        Args:
            message: Log message
            container_id: Optional container ID
            event_type: Optional event type
            error: Optional exception object
            **kwargs: Additional fields
        """
        if error:
            kwargs.update({
                'error_type': type(error).__name__,
                'error_message': str(error)
            })
        
        log_entry = self._create_log_entry('error', message, container_id, event_type, **kwargs)
        self.logger.error(json.dumps(log_entry))
    
    def debug(
        self,
        message: str,
        container_id: Optional[str] = None,
        event_type: Optional[str] = None,
        **kwargs
    ) -> None:
        """Log debug level message with structured data.
        
        Args:
            message: Log message
            container_id: Optional container ID
            event_type: Optional event type
            **kwargs: Additional fields
        """
        log_entry = self._create_log_entry('debug', message, container_id, event_type, **kwargs)
        self.logger.debug(json.dumps(log_entry))
    
    def scaling_event(
        self,
        container_id: str,
        event_type: str,
        details: Dict[str, Any],
        success: bool = True
    ) -> None:
        """Log a scaling event with structured data.
        
        Args:
            container_id: Container ID
            event_type: Type of scaling event
            details: Event details
            success: Whether the event was successful
        """
        log_entry = self._create_log_entry(
            'info' if success else 'error',
            f"Scaling event: {event_type}",
            container_id=container_id,
            event_type=event_type,
            success=success,
            details=details
        )
        
        if success:
            self.logger.info(json.dumps(log_entry))
        else:
            self.logger.error(json.dumps(log_entry))
    
    def performance_metrics(
        self,
        container_id: str,
        cpu_usage: float,
        memory_usage: float,
        memory_total: int,
        cpu_cores: int,
        **kwargs
    ) -> None:
        """Log performance metrics with structured data.
        
        Args:
            container_id: Container ID
            cpu_usage: CPU usage percentage
            memory_usage: Memory usage in MB
            memory_total: Total allocated memory in MB
            cpu_cores: Number of CPU cores
            **kwargs: Additional metrics
        """
        metrics = {
            'cpu_usage_percent': round(cpu_usage, 2),
            'memory_usage_mb': round(memory_usage, 2),
            'memory_total_mb': memory_total,
            'memory_utilization_percent': round((memory_usage / memory_total) * 100, 2),
            'cpu_cores': cpu_cores,
            **kwargs
        }
        
        log_entry = self._create_log_entry(
            'info',
            'Performance metrics collected',
            container_id=container_id,
            event_type='performance_metrics',
            metrics=metrics
        )
        
        self.logger.info(json.dumps(log_entry))
    
    def system_event(
        self,
        event_type: str,
        message: str,
        **kwargs
    ) -> None:
        """Log a system-level event.
        
        Args:
            event_type: Type of system event
            message: Event message
            **kwargs: Additional event data
        """
        log_entry = self._create_log_entry(
            'info',
            message,
            event_type=event_type,
            **kwargs
        )
        
        self.logger.info(json.dumps(log_entry))


class MetricsLogger:
    """Specialized logger for performance and operational metrics."""
    
    def __init__(self):
        """Initialize metrics logger."""
        self.structured_logger = StructuredLogger('lxc_autoscale.metrics')
    
    def log_scaling_metrics(
        self,
        total_containers: int,
        scaled_containers: int,
        scale_up_count: int,
        scale_down_count: int,
        horizontal_scale_events: int,
        cycle_duration: float
    ) -> None:
        """Log scaling cycle metrics.
        
        Args:
            total_containers: Total number of containers processed
            scaled_containers: Number of containers that were scaled
            scale_up_count: Number of scale-up operations
            scale_down_count: Number of scale-down operations
            horizontal_scale_events: Number of horizontal scaling events
            cycle_duration: Duration of the scaling cycle in seconds
        """
        self.structured_logger.system_event(
            'scaling_cycle_metrics',
            'Scaling cycle completed',
            total_containers=total_containers,
            scaled_containers=scaled_containers,
            scale_up_count=scale_up_count,
            scale_down_count=scale_down_count,
            horizontal_scale_events=horizontal_scale_events,
            cycle_duration_seconds=round(cycle_duration, 2)
        )
    
    def log_resource_utilization(
        self,
        total_cpu_cores: int,
        available_cpu_cores: int,
        total_memory_mb: int,
        available_memory_mb: int,
        container_count: int
    ) -> None:
        """Log resource utilization metrics.
        
        Args:
            total_cpu_cores: Total CPU cores in system
            available_cpu_cores: Available CPU cores
            total_memory_mb: Total memory in MB
            available_memory_mb: Available memory in MB
            container_count: Number of active containers
        """
        cpu_utilization = round(((total_cpu_cores - available_cpu_cores) / total_cpu_cores) * 100, 2)
        memory_utilization = round(((total_memory_mb - available_memory_mb) / total_memory_mb) * 100, 2)
        
        self.structured_logger.system_event(
            'resource_utilization',
            'System resource utilization',
            total_cpu_cores=total_cpu_cores,
            available_cpu_cores=available_cpu_cores,
            cpu_utilization_percent=cpu_utilization,
            total_memory_mb=total_memory_mb,
            available_memory_mb=available_memory_mb,
            memory_utilization_percent=memory_utilization,
            container_count=container_count
        )
    
    def log_error_metrics(
        self,
        error_type: str,
        error_count: int,
        affected_containers: list,
        error_details: Optional[str] = None
    ) -> None:
        """Log error occurrence metrics.
        
        Args:
            error_type: Type of error
            error_count: Number of occurrences
            affected_containers: List of affected container IDs
            error_details: Optional error details
        """
        self.structured_logger.system_event(
            'error_metrics',
            f'Error occurred: {error_type}',
            error_type=error_type,
            error_count=error_count,
            affected_containers=affected_containers,
            error_details=error_details
        )


# Global instances for easy access
structured_logger = StructuredLogger()
metrics_logger = MetricsLogger()