"""Metrics calculation utilities for container scaling decisions."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Tuple

from constants import (
    AGGRESSIVE_MULTIPLIER, BEHAVIOR_AGGRESSIVE, BEHAVIOR_CONSERVATIVE,
    CONSERVATIVE_MULTIPLIER, DEFAULT_CPU_SCALE_DIVISOR, OFF_PEAK_MULTIPLIER
)


class MetricsCalculator:
    """Handles calculation of scaling metrics and thresholds."""
    
    def __init__(self, config_manager):
        """Initialize metrics calculator.
        
        Args:
            config_manager: Configuration manager instance
        """
        self.config_manager = config_manager
    
    def calculate_increment(
        self,
        current: float,
        upper_threshold: float,
        min_increment: int,
        max_increment: int
    ) -> int:
        """Calculate the increment for resource scaling based on current usage and thresholds.
        
        Args:
            current: Current usage percentage
            upper_threshold: The upper threshold for usage
            min_increment: Minimum increment value
            max_increment: Maximum increment value
            
        Returns:
            Calculated increment value
        """
        cpu_scale_divisor = self.config_manager.get_default('cpu_scale_divisor', DEFAULT_CPU_SCALE_DIVISOR)
        proportional_increment = int((current - upper_threshold) / cpu_scale_divisor)
        
        calculated_increment = min(max(min_increment, proportional_increment), max_increment)
        
        logging.debug(
            f"Calculated increment: {calculated_increment} "
            f"(current: {current}, upper_threshold: {upper_threshold}, "
            f"min_increment: {min_increment}, max_increment: {max_increment})"
        )
        
        return calculated_increment
    
    def calculate_decrement(
        self,
        current: float,
        lower_threshold: float,
        current_allocated: int,
        min_decrement: int,
        min_allocated: int
    ) -> int:
        """Calculate the decrement for resource scaling based on current usage and thresholds.
        
        Args:
            current: Current usage percentage
            lower_threshold: The lower threshold for usage
            current_allocated: Currently allocated resources (CPU or memory)
            min_decrement: Minimum decrement value
            min_allocated: Minimum allowed allocated resources
            
        Returns:
            Calculated decrement value
        """
        cpu_scale_divisor = self.config_manager.get_default('cpu_scale_divisor', DEFAULT_CPU_SCALE_DIVISOR)
        dynamic_decrement = max(1, int((lower_threshold - current) / cpu_scale_divisor))
        
        # Ensure we don't go below minimum allocated resources
        max_possible_decrement = current_allocated - min_allocated
        calculated_decrement = max(min(max_possible_decrement, dynamic_decrement), min_decrement)
        
        logging.debug(
            f"Calculated decrement: {calculated_decrement} "
            f"(current: {current}, lower_threshold: {lower_threshold}, "
            f"current_allocated: {current_allocated}, min_decrement: {min_decrement}, "
            f"min_allocated: {min_allocated})"
        )
        
        return calculated_decrement
    
    def get_behavior_multiplier(self) -> float:
        """Determine the behavior multiplier based on configuration and time.
        
        Returns:
            The behavior multiplier with dynamic adjustment
        """
        behavior = self.config_manager.get_default('behaviour', 'normal')
        base_multiplier = 1.0
        
        if behavior == BEHAVIOR_CONSERVATIVE:
            base_multiplier = CONSERVATIVE_MULTIPLIER
        elif behavior == BEHAVIOR_AGGRESSIVE:
            base_multiplier = AGGRESSIVE_MULTIPLIER
        
        # Apply time-based adjustment for off-peak hours
        if self.is_off_peak():
            base_multiplier *= OFF_PEAK_MULTIPLIER
        
        logging.debug(f"Behavior multiplier set to {base_multiplier} based on configuration and time")
        return base_multiplier
    
    def is_off_peak(self) -> bool:
        """Determine if the current time is within off-peak hours.
        
        Returns:
            True if it is off-peak, otherwise False
        """
        current_hour = datetime.now().hour
        start = self.config_manager.get_default('off_peak_start', 22)
        end = self.config_manager.get_default('off_peak_end', 6)
        
        logging.debug(f"Current hour: {current_hour}, Off-peak hours: {start} - {end}")
        
        if start < end:
            return start <= current_hour < end
        else:
            return current_hour >= start or current_hour < end
    
    def calculate_dynamic_thresholds(
        self,
        container_history: List[Dict[str, Any]]
    ) -> Tuple[float, float]:
        """Calculate dynamic thresholds based on historical usage patterns.
        
        Args:
            container_history: List of historical usage data points
            
        Returns:
            Tuple containing dynamic lower and upper thresholds
        """
        if not container_history:
            return (
                self.config_manager.get_default('cpu_lower_threshold', 20),
                self.config_manager.get_default('cpu_upper_threshold', 80)
            )
        
        usage_values = [point['cpu_usage'] for point in container_history]
        avg_usage = sum(usage_values) / len(usage_values)
        
        # Calculate standard deviation
        variance = sum((x - avg_usage) ** 2 for x in usage_values) / len(usage_values)
        std_dev = variance ** 0.5
        
        # Calculate dynamic thresholds
        default_lower = self.config_manager.get_default('cpu_lower_threshold', 20)
        default_upper = self.config_manager.get_default('cpu_upper_threshold', 80)
        
        dynamic_lower = max(default_lower, avg_usage - std_dev)
        dynamic_upper = min(default_upper, avg_usage + std_dev * 1.5)
        
        logging.debug(
            f"Dynamic thresholds calculated: lower={dynamic_lower:.2f}, upper={dynamic_upper:.2f} "
            f"(avg_usage={avg_usage:.2f}, std_dev={std_dev:.2f})"
        )
        
        return dynamic_lower, dynamic_upper
    
    def calculate_group_metrics(
        self,
        group_containers: List[str],
        containers_data: Dict[str, Dict[str, Any]]
    ) -> Dict[str, float]:
        """Calculate metrics for a horizontal scaling group.
        
        Args:
            group_containers: List of container IDs in the group
            containers_data: Container resource usage data
            
        Returns:
            Dictionary containing group metrics
        """
        if not group_containers:
            return {
                'avg_cpu_usage': 0.0,
                'avg_mem_usage': 0.0,
                'total_containers': 0
            }
        
        total_cpu = sum(containers_data[ctid]['cpu'] for ctid in group_containers if ctid in containers_data)
        total_mem = sum(containers_data[ctid]['mem'] for ctid in group_containers if ctid in containers_data)
        num_containers = len([ctid for ctid in group_containers if ctid in containers_data])
        
        if num_containers == 0:
            return {
                'avg_cpu_usage': 0.0,
                'avg_mem_usage': 0.0,
                'total_containers': 0
            }
        
        metrics = {
            'avg_cpu_usage': total_cpu / num_containers,
            'avg_mem_usage': total_mem / num_containers,
            'total_containers': num_containers
        }
        
        logging.debug(f"Group metrics calculated: {metrics}")
        return metrics
    
    def calculate_resource_utilization(
        self,
        containers_data: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Dict[str, float]]:
        """Calculate resource utilization metrics for all containers.
        
        Args:
            containers_data: Container resource usage data
            
        Returns:
            Dictionary containing utilization metrics for each container
        """
        utilization_metrics = {}
        
        for ctid, data in containers_data.items():
            cpu_usage = data.get('cpu', 0)
            mem_usage = data.get('mem', 0)
            total_memory = data.get('initial_memory', 1)  # Avoid division by zero
            total_cores = data.get('initial_cores', 1)
            
            utilization_metrics[ctid] = {
                'cpu_utilization_percent': round(cpu_usage, 2),
                'memory_utilization_percent': round((mem_usage / total_memory) * 100, 2),
                'memory_free_percent': round(100 - ((mem_usage / total_memory) * 100), 2),
                'cores_allocated': total_cores,
                'memory_allocated_mb': total_memory,
                'memory_used_mb': round(mem_usage, 2)
            }
        
        return utilization_metrics