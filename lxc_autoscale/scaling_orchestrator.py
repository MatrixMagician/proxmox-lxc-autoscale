"""Main orchestrator for LXC container scaling operations."""

import logging
from datetime import datetime
from typing import Any, Dict

from command_executor import CommandExecutor
from config_manager import config_manager
from error_handler import ErrorHandler, safe_execute
from horizontal_scaler import HorizontalScaler
from lxc_utils import log_json_event
from metrics_calculator import MetricsCalculator
from notification import send_notification
from resource_scaler import ResourceScaler


class ScalingOrchestrator:
    """Orchestrates all scaling operations for LXC containers."""
    
    def __init__(self):
        """Initialize the scaling orchestrator with all required components."""
        self.config_manager = config_manager
        self.command_executor = CommandExecutor(self.config_manager)
        self.metrics_calculator = MetricsCalculator(self.config_manager)
        self.resource_scaler = ResourceScaler(
            self.config_manager, self.command_executor, self.metrics_calculator
        )
        self.horizontal_scaler = HorizontalScaler(
            self.config_manager, self.command_executor, self.metrics_calculator
        )
    
    def process_scaling_cycle(
        self,
        containers_data: Dict[str, Dict[str, Any]],
        energy_mode: bool = False
    ) -> None:
        """Process a complete scaling cycle for all containers.
        
        Args:
            containers_data: Dictionary containing container resource usage data
            energy_mode: Flag to enable energy efficiency mode during off-peak hours
        """
        cycle_start_time = datetime.now()
        logging.info(f"Starting scaling cycle at {cycle_start_time.isoformat()}")
        
        try:
            # Log cycle overview
            self._log_cycle_overview(containers_data, energy_mode)
            
            # Collect performance metrics for analysis
            self._collect_performance_metrics(containers_data)
            
            # Perform vertical scaling (resource adjustment)
            vertical_start = datetime.now()
            logging.info("Starting vertical scaling operations...")
            self.resource_scaler.adjust_resources(containers_data, energy_mode)
            vertical_duration = (datetime.now() - vertical_start).total_seconds()
            logging.info(f"Vertical scaling completed in {vertical_duration:.2f} seconds")
            
            # Perform horizontal scaling
            horizontal_start = datetime.now()
            logging.info("Starting horizontal scaling operations...")
            self.horizontal_scaler.manage_horizontal_scaling(containers_data)
            horizontal_duration = (datetime.now() - horizontal_start).total_seconds()
            logging.info(f"Horizontal scaling completed in {horizontal_duration:.2f} seconds")
            
            # Log cycle completion
            total_duration = (datetime.now() - cycle_start_time).total_seconds()
            logging.info(f"Scaling cycle completed in {total_duration:.2f} seconds")
            
            # Log cycle summary
            self._log_cycle_summary(containers_data, total_duration, vertical_duration, horizontal_duration)
            
        except Exception as e:
            logging.exception(f"Error during scaling cycle: {e}")
            ErrorHandler.handle_recoverable_error(e, "scaling cycle")
            
            # Send error notification
            send_notification(
                "Scaling Cycle Error",
                f"An error occurred during the scaling cycle: {str(e)}",
                priority=8
            )
    
    def _log_cycle_overview(
        self,
        containers_data: Dict[str, Dict[str, Any]],
        energy_mode: bool
    ) -> None:
        """Log overview information for the scaling cycle.
        
        Args:
            containers_data: Container resource usage data
            energy_mode: Energy efficiency mode flag
        """
        total_containers = len(containers_data)
        ignored_containers = sum(1 for ctid in containers_data.keys() if self.config_manager.is_ignored(ctid))
        active_containers = total_containers - ignored_containers
        
        is_off_peak = self.metrics_calculator.is_off_peak()
        behavior_mode = self.config_manager.get_default('behaviour', 'normal')
        
        logging.info(
            f"Scaling cycle overview:\n"
            f"  Total containers: {total_containers}\n"
            f"  Active containers: {active_containers}\n"
            f"  Ignored containers: {ignored_containers}\n"
            f"  Energy mode: {'enabled' if energy_mode else 'disabled'}\n"
            f"  Off-peak hours: {'yes' if is_off_peak else 'no'}\n"
            f"  Behavior mode: {behavior_mode}\n"
            f"  Behavior multiplier: {self.metrics_calculator.get_behavior_multiplier():.2f}"
        )
        
        # Log horizontal scaling groups
        scaling_groups = self.config_manager.get_horizontal_scaling_groups()
        if scaling_groups:
            logging.info(f"Active horizontal scaling groups: {len(scaling_groups)}")
            for group_name, group_config in scaling_groups.items():
                containers_in_group = len(group_config.get('lxc_containers', []))
                logging.info(f"  {group_name}: {containers_in_group} containers")
    
    def _collect_performance_metrics(self, containers_data: Dict[str, Dict[str, Any]]) -> None:
        """Collect and log performance metrics for analysis.
        
        Args:
            containers_data: Container resource usage data
        """
        for ctid, usage_data in containers_data.items():
            if self.config_manager.is_ignored(ctid):
                continue
            
            # Calculate utilization metrics
            utilization_metrics = self.metrics_calculator.calculate_resource_utilization({ctid: usage_data})
            container_metrics = utilization_metrics.get(ctid, {})
            
            # Add timestamp and container info
            metrics = {
                'timestamp': datetime.now().isoformat(),
                'container_id': ctid,
                **container_metrics,
                'tier_name': self.config_manager.get_tier_config(ctid).get('tier_name', 'default')
            }
            
            # Log performance metrics
            safe_execute(
                log_json_event,
                ctid, 'performance_metrics', metrics,
                log_errors=True
            )
    
    def _log_cycle_summary(
        self,
        containers_data: Dict[str, Dict[str, Any]],
        total_duration: float,
        vertical_duration: float,
        horizontal_duration: float
    ) -> None:
        """Log summary information for the completed scaling cycle.
        
        Args:
            containers_data: Container resource usage data
            total_duration: Total cycle duration in seconds
            vertical_duration: Vertical scaling duration in seconds
            horizontal_duration: Horizontal scaling duration in seconds
        """
        summary_data = {
            'timestamp': datetime.now().isoformat(),
            'total_containers_processed': len(containers_data),
            'active_containers': len([
                ctid for ctid in containers_data.keys()
                if not self.config_manager.is_ignored(ctid)
            ]),
            'total_duration_seconds': round(total_duration, 2),
            'vertical_scaling_duration_seconds': round(vertical_duration, 2),
            'horizontal_scaling_duration_seconds': round(horizontal_duration, 2),
            'energy_mode_active': self.metrics_calculator.is_off_peak(),
            'behavior_mode': self.config_manager.get_default('behaviour', 'normal'),
            'behavior_multiplier': self.metrics_calculator.get_behavior_multiplier()
        }
        
        # Calculate resource utilization statistics
        cpu_usages = []
        memory_utilizations = []
        
        for ctid, usage_data in containers_data.items():
            if not self.config_manager.is_ignored(ctid):
                cpu_usages.append(usage_data.get('cpu', 0))
                mem_usage = usage_data.get('mem', 0)
                mem_total = usage_data.get('initial_memory', 1)
                memory_utilizations.append((mem_usage / mem_total) * 100)
        
        if cpu_usages:
            summary_data.update({
                'avg_cpu_usage_percent': round(sum(cpu_usages) / len(cpu_usages), 2),
                'max_cpu_usage_percent': round(max(cpu_usages), 2),
                'min_cpu_usage_percent': round(min(cpu_usages), 2)
            })
        
        if memory_utilizations:
            summary_data.update({
                'avg_memory_utilization_percent': round(sum(memory_utilizations) / len(memory_utilizations), 2),
                'max_memory_utilization_percent': round(max(memory_utilizations), 2),
                'min_memory_utilization_percent': round(min(memory_utilizations), 2)
            })
        
        # Log the summary
        safe_execute(
            log_json_event,
            'system', 'scaling_cycle_summary', summary_data,
            log_errors=True
        )
        
        logging.info(f"Scaling cycle summary: {summary_data}")
    
    def send_detailed_notification(
        self,
        ctid: str,
        event_type: str,
        details: Dict[str, Any]
    ) -> None:
        """Send detailed notifications with enhanced context.
        
        Args:
            ctid: Container ID
            event_type: Type of scaling event
            details: Dictionary containing event details
        """
        tier_config = self.config_manager.get_tier_config(ctid)
        
        message = f"Container {ctid} - {event_type}\n"
        message += f"Current Settings:\n"
        message += f"  - CPU Thresholds: {tier_config['cpu_lower_threshold']}% - {tier_config['cpu_upper_threshold']}%\n"
        message += f"  - Memory Thresholds: {tier_config['memory_lower_threshold']}% - {tier_config['memory_upper_threshold']}%\n"
        message += f"  - Tier: {tier_config.get('tier_name', 'default')}\n"
        
        for key, value in details.items():
            message += f"  - {key}: {value}\n"
        
        safe_execute(
            send_notification,
            f"{event_type} - Container {ctid}",
            message,
            log_errors=True
        )
        
        safe_execute(
            log_json_event,
            ctid, event_type, details,
            log_errors=True
        )
    
    def validate_system_readiness(self) -> bool:
        """Validate that the system is ready for scaling operations.
        
        Returns:
            True if system is ready, False otherwise
        """
        try:
            # Test command executor
            test_result = self.command_executor.execute('echo "test"')
            if test_result != "test":
                logging.error("Command executor validation failed")
                return False
            
            # Validate configuration
            required_defaults = ['reserve_cpu_percent', 'reserve_memory_mb']
            for key in required_defaults:
                if self.config_manager.get_default(key) is None:
                    logging.error(f"Missing required configuration: {key}")
                    return False
            
            # Test if we can get basic system information
            from lxc_utils import get_total_cores, get_total_memory
            
            if get_total_cores() <= 0 or get_total_memory() <= 0:
                logging.error("Unable to retrieve system resource information")
                return False
            
            logging.info("System readiness validation passed")
            return True
            
        except Exception as e:
            logging.error(f"System readiness validation failed: {e}")
            return False
    
    def cleanup(self) -> None:
        """Cleanup resources used by the orchestrator."""
        try:
            self.command_executor.close_ssh_connection()
            logging.info("Scaling orchestrator cleanup completed")
        except Exception as e:
            logging.error(f"Error during orchestrator cleanup: {e}")
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.cleanup()