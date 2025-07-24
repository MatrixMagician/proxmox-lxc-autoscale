"""Horizontal scaling management for LXC containers."""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

from constants import (
    DEFAULT_SCALE_IN_GRACE_PERIOD, DEFAULT_SCALE_OUT_GRACE_PERIOD,
    DEFAULT_TIMEOUT_EXTENDED, NETWORK_TYPE_DHCP, NETWORK_TYPE_STATIC
)
from error_handler import ScalingError, safe_execute
from lxc_utils import generate_cloned_hostname, generate_unique_snapshot_name, log_json_event
from notification import send_notification


class HorizontalScaler:
    """Manages horizontal scaling operations for container groups."""
    
    def __init__(self, config_manager, command_executor, metrics_calculator):
        """Initialize horizontal scaler.
        
        Args:
            config_manager: Configuration manager instance
            command_executor: Command executor instance
            metrics_calculator: Metrics calculator instance
        """
        self.config_manager = config_manager
        self.command_executor = command_executor
        self.metrics_calculator = metrics_calculator
        self.scale_last_action: Dict[str, datetime] = {}
    
    def manage_horizontal_scaling(self, containers_data: Dict[str, Dict[str, Any]]) -> None:
        """Manage horizontal scaling for all configured groups.
        
        Args:
            containers_data: Container resource usage data
        """
        scaling_groups = self.config_manager.get_horizontal_scaling_groups()
        
        for group_name, group_config in scaling_groups.items():
            try:
                self._process_scaling_group(group_name, group_config, containers_data)
            except Exception as e:
                logging.exception(f"Error in horizontal scaling for group {group_name}: {e}")
                self._log_scaling_event(group_name, 'horizontal_scaling_error', {
                    'error': str(e),
                    'group_config': group_config
                }, error=True)
    
    def _process_scaling_group(
        self,
        group_name: str,
        group_config: Dict[str, Any],
        containers_data: Dict[str, Dict[str, Any]]
    ) -> None:
        """Process scaling decisions for a single group.
        
        Args:
            group_name: Name of the scaling group
            group_config: Group configuration
            containers_data: Container resource usage data
        """
        current_time = datetime.now()
        last_action_time = self.scale_last_action.get(
            group_name,
            current_time - timedelta(hours=1)
        )
        
        # Get active containers in the group
        group_containers = [
            ctid for ctid in group_config['lxc_containers']
            if ctid in containers_data and not self.config_manager.is_ignored(ctid)
        ]
        
        if not group_containers:
            self._log_scaling_event(group_name, 'horizontal_scaling_skip', {
                'reason': 'No active containers in group'
            })
            return
        
        # Calculate group metrics
        metrics = self.metrics_calculator.calculate_group_metrics(group_containers, containers_data)
        self._log_scaling_event(group_name, 'group_metrics', metrics)
        
        # Make scaling decisions
        if self._should_scale_out(metrics, group_config, current_time, last_action_time):
            self._scale_out(group_name, group_config)
            self.scale_last_action[group_name] = current_time
        elif self._should_scale_in(metrics, group_config, current_time, last_action_time):
            self._scale_in(group_name, group_config)
            self.scale_last_action[group_name] = current_time
    
    def _should_scale_out(
        self,
        metrics: Dict[str, float],
        group_config: Dict[str, Any],
        current_time: datetime,
        last_action_time: datetime
    ) -> bool:
        """Determine if the group should scale out.
        
        Args:
            metrics: Group metrics
            group_config: Group configuration
            current_time: Current timestamp
            last_action_time: Last scaling action timestamp
            
        Returns:
            True if group should scale out
        """
        grace_period = group_config.get('scale_out_grace_period', DEFAULT_SCALE_OUT_GRACE_PERIOD)
        
        # Check grace period
        if current_time - last_action_time < timedelta(seconds=grace_period):
            logging.debug(f"Scale out blocked by grace period for group")
            return False
        
        # Check if max instances reached
        current_instances = len(group_config.get('lxc_containers', []))
        max_instances = group_config.get('max_instances', float('inf'))
        
        if current_instances >= max_instances:
            logging.info(f"Max instances ({max_instances}) reached, scale out blocked")
            return False
        
        # Check thresholds
        cpu_threshold = group_config.get('horiz_cpu_upper_threshold', 80)
        memory_threshold = group_config.get('horiz_memory_upper_threshold', 80)
        
        should_scale = (
            metrics['avg_cpu_usage'] > cpu_threshold or
            metrics['avg_mem_usage'] > memory_threshold
        )
        
        if should_scale:
            logging.info(
                f"Scale out triggered - CPU: {metrics['avg_cpu_usage']:.1f}% "
                f"(threshold: {cpu_threshold}%), Memory: {metrics['avg_mem_usage']:.1f}% "
                f"(threshold: {memory_threshold}%)"
            )
        
        return should_scale
    
    def _should_scale_in(
        self,
        metrics: Dict[str, float],
        group_config: Dict[str, Any],
        current_time: datetime,
        last_action_time: datetime
    ) -> bool:
        """Determine if the group should scale in.
        
        Args:
            metrics: Group metrics
            group_config: Group configuration
            current_time: Current timestamp
            last_action_time: Last scaling action timestamp
            
        Returns:
            True if group should scale in
        """
        grace_period = group_config.get('scale_in_grace_period', DEFAULT_SCALE_IN_GRACE_PERIOD)
        
        # Check grace period
        if current_time - last_action_time < timedelta(seconds=grace_period):
            logging.debug(f"Scale in blocked by grace period for group")
            return False
        
        # Check minimum instances
        min_instances = group_config.get('min_containers', 1)
        
        if metrics['total_containers'] <= min_instances:
            logging.debug(f"Minimum instances ({min_instances}) reached, scale in blocked")
            return False
        
        # Check thresholds
        cpu_threshold = group_config.get('horiz_cpu_lower_threshold', 20)
        memory_threshold = group_config.get('horiz_memory_lower_threshold', 20)
        
        should_scale = (
            metrics['avg_cpu_usage'] < cpu_threshold and
            metrics['avg_mem_usage'] < memory_threshold
        )
        
        if should_scale:
            logging.info(
                f"Scale in triggered - CPU: {metrics['avg_cpu_usage']:.1f}% "
                f"(threshold: {cpu_threshold}%), Memory: {metrics['avg_mem_usage']:.1f}% "
                f"(threshold: {memory_threshold}%)"
            )
        
        return should_scale
    
    def _scale_out(self, group_name: str, group_config: Dict[str, Any]) -> None:
        """Scale out a horizontal scaling group by cloning a new container.
        
        Args:
            group_name: The name of the scaling group
            group_config: Configuration details for the scaling group
        """
        try:
            current_instances = sorted(map(int, group_config['lxc_containers']))
            starting_clone_id = group_config['starting_clone_id']
            max_instances = group_config['max_instances']
            
            # Check if the maximum number of instances has been reached
            if len(current_instances) >= max_instances:
                logging.info(f"Max instances reached for {group_name}. No scale out performed.")
                return
            
            # Determine the next available clone ID
            new_ctid = starting_clone_id + len([
                ctid for ctid in current_instances if int(ctid) >= starting_clone_id
            ])
            
            base_snapshot = group_config['base_snapshot_name']
            unique_snapshot_name = generate_unique_snapshot_name("snap")
            
            # Create snapshot
            if not self._create_snapshot(base_snapshot, unique_snapshot_name):
                return
            
            # Clone container
            if not self._clone_container(base_snapshot, new_ctid, unique_snapshot_name, group_config):
                return
            
            # Configure networking
            self._configure_networking(new_ctid, group_config, current_instances)
            
            # Start the new container
            start_result = self.command_executor.execute_proxmox_command(f"pct start {new_ctid}")
            if start_result is not None:
                # Update tracking
                current_instances.append(new_ctid)
                group_config['lxc_containers'] = set(map(str, current_instances))
                
                clone_hostname = generate_cloned_hostname(base_snapshot, len(current_instances))
                
                logging.info(f"Container {new_ctid} started successfully as part of {group_name}")
                send_notification(
                    f"Scale Out: {group_name}",
                    f"New container {new_ctid} with hostname {clone_hostname} started."
                )
                
                log_json_event(
                    new_ctid,
                    "Scale Out",
                    f"Container {base_snapshot} cloned to {new_ctid}. {new_ctid} started."
                )
            else:
                logging.error(f"Failed to start container {new_ctid}")
                
        except Exception as e:
            logging.exception(f"Error during scale out for group {group_name}: {e}")
            raise ScalingError(f"Scale out failed for group {group_name}: {e}") from e
    
    def _scale_in(self, group_name: str, group_config: Dict[str, Any]) -> None:
        """Scale in a horizontal scaling group by removing a container.
        
        Args:
            group_name: The name of the scaling group
            group_config: Configuration details for the scaling group
        """
        try:
            current_instances = list(group_config['lxc_containers'])
            min_instances = group_config.get('min_containers', 1)
            
            if len(current_instances) <= min_instances:
                logging.info(f"Minimum instances reached for {group_name}. No scale in performed.")
                return
            
            # Find the container to remove (typically the newest one)
            container_to_remove = max(current_instances, key=int)
            
            # Stop the container
            stop_result = self.command_executor.execute_proxmox_command(f"pct stop {container_to_remove}")
            if stop_result is not None:
                # Remove from tracking
                group_config['lxc_containers'].discard(container_to_remove)
                
                logging.info(f"Container {container_to_remove} scaled in from {group_name}")
                send_notification(
                    f"Scale In: {group_name}",
                    f"Container {container_to_remove} stopped and removed from group."
                )
                
                log_json_event(
                    container_to_remove,
                    "Scale In",
                    f"Container {container_to_remove} scaled in from group {group_name}"
                )
            else:
                logging.error(f"Failed to stop container {container_to_remove} for scale in")
                
        except Exception as e:
            logging.exception(f"Error during scale in for group {group_name}: {e}")
            raise ScalingError(f"Scale in failed for group {group_name}: {e}") from e
    
    def _create_snapshot(self, base_container: str, snapshot_name: str) -> bool:
        """Create a snapshot of the base container.
        
        Args:
            base_container: Base container ID
            snapshot_name: Name for the snapshot
            
        Returns:
            True if snapshot was created successfully
        """
        logging.info(f"Creating snapshot {snapshot_name} of container {base_container}...")
        
        snapshot_cmd = f"pct snapshot {base_container} {snapshot_name} --description 'Auto snapshot for scaling'"
        result = self.command_executor.execute_proxmox_command(snapshot_cmd)
        
        if result is not None:
            logging.info(f"Snapshot {snapshot_name} created successfully")
            return True
        else:
            logging.error(f"Failed to create snapshot {snapshot_name} of container {base_container}")
            return False
    
    def _clone_container(
        self,
        base_container: str,
        new_ctid: int,
        snapshot_name: str,
        group_config: Dict[str, Any]
    ) -> bool:
        """Clone a container from snapshot.
        
        Args:
            base_container: Base container ID
            new_ctid: New container ID
            snapshot_name: Snapshot name to clone from
            group_config: Group configuration
            
        Returns:
            True if clone was successful
        """
        logging.info(f"Cloning container {base_container} to create {new_ctid} using snapshot {snapshot_name}...")
        
        clone_hostname = generate_cloned_hostname(base_container, new_ctid)
        clone_cmd = f"pct clone {base_container} {new_ctid} --snapname {snapshot_name} --hostname {clone_hostname}"
        
        extended_timeout = self.config_manager.get_default('timeout_extended', DEFAULT_TIMEOUT_EXTENDED)
        result = self.command_executor.execute_proxmox_command(clone_cmd, timeout=extended_timeout)
        
        if result is not None:
            logging.info(f"Container {new_ctid} cloned successfully")
            return True
        else:
            logging.error(f"Failed to clone container {base_container} using snapshot {snapshot_name}")
            return False
    
    def _configure_networking(
        self,
        ctid: int,
        group_config: Dict[str, Any],
        current_instances: List[int]
    ) -> None:
        """Configure networking for the new container.
        
        Args:
            ctid: Container ID
            group_config: Group configuration
            current_instances: List of current container instances
        """
        network_type = group_config.get('clone_network_type', NETWORK_TYPE_DHCP)
        
        if network_type == NETWORK_TYPE_DHCP:
            net_cmd = f"pct set {ctid} -net0 name=eth0,bridge=vmbr0,ip=dhcp"
            self.command_executor.execute_proxmox_command(net_cmd)
            logging.info(f"Configured DHCP networking for container {ctid}")
            
        elif network_type == NETWORK_TYPE_STATIC:
            static_ip_range = group_config.get('static_ip_range', [])
            if static_ip_range:
                available_ips = [
                    ip for ip in static_ip_range
                    if ip not in [str(instance) for instance in current_instances]
                ]
                if available_ips:
                    ip_address = available_ips[0]
                    net_cmd = f"pct set {ctid} -net0 name=eth0,bridge=vmbr0,ip={ip_address}/24"
                    self.command_executor.execute_proxmox_command(net_cmd)
                    logging.info(f"Configured static IP {ip_address} for container {ctid}")
                else:
                    logging.warning("No available IPs in the specified range for static IP assignment")
            else:
                logging.warning("Static IP range not configured, falling back to DHCP")
                net_cmd = f"pct set {ctid} -net0 name=eth0,bridge=vmbr0,ip=dhcp"
                self.command_executor.execute_proxmox_command(net_cmd)
    
    def _log_scaling_event(
        self,
        group_name: str,
        event_type: str,
        details: Dict[str, Any],
        error: bool = False
    ) -> None:
        """Log scaling events with structured data.
        
        Args:
            group_name: Name of the scaling group
            event_type: Type of scaling event
            details: Dictionary containing event details
            error: Boolean indicating if this is an error event
        """
        log_level = logging.ERROR if error else logging.INFO
        structured_log = {
            'timestamp': datetime.now().isoformat(),
            'group_name': group_name,
            'event_type': event_type,
            'details': details
        }
        
        logging.log(log_level, f"Horizontal scaling event for group {group_name}: {event_type}")
        log_json_event(group_name, event_type, structured_log)
        
        if error:
            send_notification(f"Horizontal Scaling Error: {group_name}", str(structured_log))