"""Resource scaling management for individual containers."""

import logging
from datetime import datetime
from typing import Any, Dict, Tuple

from constants import DEFAULT_MEMORY_SCALE_FACTOR
from error_handler import ScalingError, ValidationError, handle_container_errors
from lxc_utils import log_json_event
from notification import send_notification


class ResourceScaler:
    """Handles vertical scaling of individual container resources."""
    
    def __init__(self, config_manager, command_executor, metrics_calculator):
        """Initialize resource scaler.
        
        Args:
            config_manager: Configuration manager instance
            command_executor: Command executor instance
            metrics_calculator: Metrics calculator instance
        """
        self.config_manager = config_manager
        self.command_executor = command_executor
        self.metrics_calculator = metrics_calculator
    
    def adjust_resources(self, containers_data: Dict[str, Dict[str, Any]], energy_mode: bool) -> None:
        """Adjust CPU and memory resources for each container based on usage.
        
        Args:
            containers_data: A dictionary of container resource usage data
            energy_mode: Flag to indicate if energy-saving adjustments should be made
        """
        logging.info("Starting resource allocation process...")
        logging.info(f"Processing {len(containers_data)} containers")
        
        # Calculate available resources
        available_cores, available_memory = self._calculate_available_resources()
        
        logging.info(f"Initial resources: {available_cores} cores, {available_memory} MB memory")
        
        # Log current resource usage for all containers
        self._log_container_status(containers_data)
        
        # Process each container
        for ctid, usage_data in containers_data.items():
            if self.config_manager.is_ignored(ctid):
                logging.info(f"Skipping ignored container {ctid}")
                continue
            
            try:
                available_cores, available_memory = self._process_container(
                    ctid, usage_data, available_cores, available_memory, energy_mode
                )
            except Exception as e:
                logging.error(f"Error processing container {ctid}: {e}")
                continue
        
        logging.info(f"Final resources: {available_cores} cores, {available_memory} MB memory")
    
    def _calculate_available_resources(self) -> Tuple[int, int]:
        """Calculate available CPU cores and memory for allocation.
        
        Returns:
            Tuple of (available_cores, available_memory)
        """
        # Import here to avoid circular imports
        from lxc_utils import get_total_cores, get_total_memory
        
        total_cores = get_total_cores()
        total_memory = get_total_memory()
        
        reserve_cpu_percent = self.config_manager.get_default('reserve_cpu_percent', 10)
        reserve_memory_mb = self.config_manager.get_default('reserve_memory_mb', 2048)
        
        reserved_cores = max(1, int(total_cores * reserve_cpu_percent / 100))
        available_cores = total_cores - reserved_cores
        available_memory = total_memory - reserve_memory_mb
        
        return available_cores, available_memory
    
    def _log_container_status(self, containers_data: Dict[str, Dict[str, Any]]) -> None:
        """Log current resource usage and tier settings for all containers.
        
        Args:
            containers_data: Container resource usage data
        """
        logging.info("Current resource usage and tier settings for all containers:")
        
        for ctid, usage in containers_data.items():
            tier_config = self.config_manager.get_tier_config(ctid)
            
            cpu_usage = round(usage['cpu'], 2)
            mem_usage = round(usage['mem'], 2)
            total_memory = usage['initial_memory']
            free_mem_percent = round(100 - ((mem_usage / total_memory) * 100), 2)
            
            logging.info(
                f"Container {ctid}:\n"
                f"  CPU usage: {cpu_usage}% "
                f"(Tier limits: {tier_config['cpu_lower_threshold']}%-{tier_config['cpu_upper_threshold']}%)\n"
                f"  Memory usage: {mem_usage}MB ({free_mem_percent}% free of {total_memory}MB total)\n"
                f"  Tier settings:\n"
                f"    Min cores: {tier_config['min_cores']}, Max cores: {tier_config['max_cores']}\n"
                f"    Min memory: {tier_config['min_memory']}MB\n"
                f"    Current cores: {usage['initial_cores']}"
            )
    
    @handle_container_errors
    def _process_container(
        self,
        ctid: str,
        usage_data: Dict[str, Any],
        available_cores: int,
        available_memory: int,
        energy_mode: bool
    ) -> Tuple[int, int]:
        """Process scaling for a single container.
        
        Args:
            ctid: Container ID
            usage_data: Container usage data
            available_cores: Available CPU cores
            available_memory: Available memory
            energy_mode: Energy efficiency mode flag
            
        Returns:
            Tuple of updated (available_cores, available_memory)
        """
        tier_config = self.config_manager.get_tier_config(ctid)
        
        # Validate tier configuration
        if not self._validate_tier_settings(ctid, tier_config):
            logging.error(f"Invalid tier settings for container {ctid}. Skipping.")
            return available_cores, available_memory
        
        logging.info(f"Processing container {ctid} with tier configuration")
        
        current_cores = usage_data["initial_cores"]
        current_memory = usage_data["initial_memory"]
        cpu_usage = usage_data['cpu']
        mem_usage = usage_data['mem']
        
        logging.info(f"Container {ctid} - CPU usage: {cpu_usage}%, Memory usage: {mem_usage}MB")
        
        # Adjust CPU cores
        available_cores = self._adjust_cpu_cores(
            ctid, cpu_usage, current_cores, available_cores, tier_config
        )
        
        # Adjust memory
        available_memory = self._adjust_memory(
            ctid, mem_usage, current_memory, available_memory, tier_config
        )
        
        # Apply energy efficiency mode if enabled
        if energy_mode and self.metrics_calculator.is_off_peak():
            available_cores, available_memory = self._apply_energy_mode(
                ctid, current_cores, current_memory, available_cores, available_memory, tier_config
            )
        
        return available_cores, available_memory
    
    def _adjust_cpu_cores(
        self,
        ctid: str,
        cpu_usage: float,
        current_cores: int,
        available_cores: int,
        tier_config: Dict[str, Any]
    ) -> int:
        """Adjust CPU cores for a container.
        
        Args:
            ctid: Container ID
            cpu_usage: Current CPU usage percentage
            current_cores: Currently allocated cores
            available_cores: Available cores
            tier_config: Tier configuration
            
        Returns:
            Updated available cores
        """
        cpu_upper = tier_config['cpu_upper_threshold']
        cpu_lower = tier_config['cpu_lower_threshold']
        min_cores = tier_config['min_cores']
        max_cores = tier_config['max_cores']
        
        if cpu_usage > cpu_upper:
            # Scale up CPU
            increment = self.metrics_calculator.calculate_increment(
                cpu_usage, cpu_upper,
                tier_config['core_min_increment'],
                tier_config['core_max_increment']
            )
            new_cores = current_cores + increment
            
            if available_cores >= increment and new_cores <= max_cores:
                result = self.command_executor.execute_proxmox_command(f"pct set {ctid} -cores {new_cores}")
                if result is not None:
                    available_cores -= increment
                    log_json_event(ctid, "Increase Cores", f"{increment}")
                    send_notification(
                        f"CPU Increased for Container {ctid}",
                        f"CPU cores increased to {new_cores}."
                    )
                    logging.info(f"Increased CPU cores for container {ctid} to {new_cores}")
                else:
                    logging.error(f"Failed to increase CPU cores for container {ctid}")
            else:
                logging.warning(f"Cannot increase CPU for container {ctid}: insufficient resources or max limit reached")
        
        elif cpu_usage < cpu_lower and current_cores > min_cores:
            # Scale down CPU
            decrement = self.metrics_calculator.calculate_decrement(
                cpu_usage, cpu_lower, current_cores,
                tier_config['core_min_increment'], min_cores
            )
            new_cores = max(min_cores, current_cores - decrement)
            
            if new_cores >= min_cores:
                result = self.command_executor.execute_proxmox_command(f"pct set {ctid} -cores {new_cores}")
                if result is not None:
                    available_cores += (current_cores - new_cores)
                    log_json_event(ctid, "Decrease Cores", f"{current_cores - new_cores}")
                    send_notification(
                        f"CPU Decreased for Container {ctid}",
                        f"CPU cores decreased to {new_cores}."
                    )
                    logging.info(f"Decreased CPU cores for container {ctid} to {new_cores}")
                else:
                    logging.error(f"Failed to decrease CPU cores for container {ctid}")
        
        return available_cores
    
    def _adjust_memory(
        self,
        ctid: str,
        mem_usage: float,
        current_memory: int,
        available_memory: int,
        tier_config: Dict[str, Any]
    ) -> int:
        """Adjust memory for a container.
        
        Args:
            ctid: Container ID
            mem_usage: Current memory usage in MB
            current_memory: Currently allocated memory
            available_memory: Available memory
            tier_config: Tier configuration
            
        Returns:
            Updated available memory
        """
        mem_upper = tier_config['memory_upper_threshold']
        mem_lower = tier_config['memory_lower_threshold']
        min_memory = tier_config['min_memory']
        
        # Convert memory usage to percentage
        mem_usage_percent = (mem_usage / current_memory) * 100
        
        behavior_multiplier = self.metrics_calculator.get_behavior_multiplier()
        memory_scale_factor = self.config_manager.get_default('memory_scale_factor', DEFAULT_MEMORY_SCALE_FACTOR)
        
        if mem_usage_percent > mem_upper:
            # Scale up memory
            increment = max(
                int(tier_config['memory_min_increment'] * behavior_multiplier),
                int((mem_usage_percent - mem_upper) * tier_config['memory_min_increment'] / memory_scale_factor)
            )
            
            if available_memory >= increment:
                new_memory = current_memory + increment
                result = self.command_executor.execute_proxmox_command(f"pct set {ctid} -memory {new_memory}")
                if result is not None:
                    available_memory -= increment
                    log_json_event(ctid, "Increase Memory", f"{increment}MB")
                    send_notification(
                        f"Memory Increased for Container {ctid}",
                        f"Memory increased by {increment}MB to {new_memory}MB."
                    )
                    logging.info(f"Increased memory for container {ctid} by {increment}MB to {new_memory}MB")
                else:
                    logging.error(f"Failed to increase memory for container {ctid}")
            else:
                logging.warning(f"Not enough available memory to increase for container {ctid}")
        
        elif mem_usage_percent < mem_lower and current_memory > min_memory:
            # Scale down memory
            decrease_amount = self.metrics_calculator.calculate_decrement(
                mem_usage_percent, mem_lower, current_memory,
                int(tier_config['min_decrease_chunk'] * behavior_multiplier), min_memory
            )
            
            if decrease_amount > 0:
                new_memory = current_memory - decrease_amount
                result = self.command_executor.execute_proxmox_command(f"pct set {ctid} -memory {new_memory}")
                if result is not None:
                    available_memory += decrease_amount
                    log_json_event(ctid, "Decrease Memory", f"{decrease_amount}MB")
                    send_notification(
                        f"Memory Decreased for Container {ctid}",
                        f"Memory decreased by {decrease_amount}MB to {new_memory}MB."
                    )
                    logging.info(f"Decreased memory for container {ctid} by {decrease_amount}MB to {new_memory}MB")
                else:
                    logging.error(f"Failed to decrease memory for container {ctid}")
        
        return available_memory
    
    def _apply_energy_mode(
        self,
        ctid: str,
        current_cores: int,
        current_memory: int,
        available_cores: int,
        available_memory: int,
        tier_config: Dict[str, Any]
    ) -> Tuple[int, int]:
        """Apply energy efficiency mode adjustments.
        
        Args:
            ctid: Container ID
            current_cores: Current CPU cores
            current_memory: Current memory
            available_cores: Available cores
            available_memory: Available memory
            tier_config: Tier configuration
            
        Returns:
            Tuple of updated (available_cores, available_memory)
        """
        min_cores = tier_config['min_cores']
        min_memory = tier_config['min_memory']
        
        # Reduce cores if above minimum
        if current_cores > min_cores:
            logging.info(f"Reducing cores for energy efficiency during off-peak hours for container {ctid}")
            result = self.command_executor.execute_proxmox_command(f"pct set {ctid} -cores {min_cores}")
            if result is not None:
                available_cores += (current_cores - min_cores)
                log_json_event(ctid, "Reduce Cores (Off-Peak)", f"{current_cores - min_cores}")
                send_notification(
                    f"CPU Reduced for Container {ctid}",
                    f"CPU cores reduced to {min_cores} for energy efficiency."
                )
        
        # Reduce memory if above minimum
        if current_memory > min_memory:
            logging.info(f"Reducing memory for energy efficiency during off-peak hours for container {ctid}")
            result = self.command_executor.execute_proxmox_command(f"pct set {ctid} -memory {min_memory}")
            if result is not None:
                available_memory += (current_memory - min_memory)
                log_json_event(ctid, "Reduce Memory (Off-Peak)", f"{current_memory - min_memory}MB")
                send_notification(
                    f"Memory Reduced for Container {ctid}",
                    f"Memory reduced to {min_memory}MB for energy efficiency."
                )
        
        return available_cores, available_memory
    
    def _validate_tier_settings(self, ctid: str, tier_config: Dict[str, Any]) -> bool:
        """Validate tier settings for a container.
        
        Args:
            ctid: Container ID
            tier_config: Tier configuration dictionary
            
        Returns:
            True if settings are valid, False otherwise
        """
        required_fields = [
            'cpu_upper_threshold', 'cpu_lower_threshold',
            'memory_upper_threshold', 'memory_lower_threshold',
            'min_cores', 'max_cores', 'min_memory'
        ]
        
        # Check for missing fields
        missing_fields = [field for field in required_fields if field not in tier_config]
        if missing_fields:
            logging.error(f"Missing required fields for container {ctid}: {', '.join(missing_fields)}")
            return False
        
        try:
            # Validate numeric ranges
            for field in required_fields:
                value = tier_config[field]
                if not isinstance(value, (int, float)) or value < 0:
                    logging.error(f"Invalid value for field '{field}' in container {ctid}: {value}")
                    return False
            
            # Validate threshold relationships
            if tier_config['cpu_lower_threshold'] >= tier_config['cpu_upper_threshold']:
                logging.error(f"CPU lower threshold must be less than upper threshold for container {ctid}")
                return False
            
            if tier_config['memory_lower_threshold'] >= tier_config['memory_upper_threshold']:
                logging.error(f"Memory lower threshold must be less than upper threshold for container {ctid}")
                return False
            
            if tier_config['min_cores'] > tier_config['max_cores']:
                logging.error(f"Minimum cores must be less than or equal to maximum cores for container {ctid}")
                return False
            
            # Validate percentage ranges
            for threshold in ['cpu_lower_threshold', 'cpu_upper_threshold', 'memory_lower_threshold', 'memory_upper_threshold']:
                value = tier_config[threshold]
                if not (0 <= value <= 100):
                    logging.error(f"Threshold '{threshold}' must be between 0 and 100 for container {ctid}")
                    return False
            
            return True
            
        except (TypeError, ValueError) as e:
            logging.error(f"Error validating tier settings for container {ctid}: {e}")
            return False