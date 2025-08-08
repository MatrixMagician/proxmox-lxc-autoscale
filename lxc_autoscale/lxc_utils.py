import json
import logging
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import paramiko
except ImportError:
    logging.error("Paramiko package not installed. SSH functionality disabled.")

try:
    from proxmox_api_client import (
        get_proxmox_client, 
        ProxmoxAPIError, 
        ProxmoxConnectionError,
        ProxmoxAuthenticationError
    )
    PROXMOX_API_AVAILABLE = True
except ImportError:
    logging.warning("Proxmox API client not available. Falling back to command execution.")
    PROXMOX_API_AVAILABLE = False

from config_manager import (BACKUP_DIR,  IGNORE_LXC, LOG_FILE,
                    LXC_TIER_ASSOCIATIONS, PROXMOX_HOSTNAME, config, get_config_value)

lock = Lock()

# Global variable to hold the SSH client
ssh_client: Optional[paramiko.SSHClient] = None



def get_containers() -> List[str]:
    """Return list of container IDs, excluding ignored ones."""
    # Try Proxmox API first if available
    if PROXMOX_API_AVAILABLE and config.get('DEFAULT', {}).get('use_proxmox_api', True):
        try:
            client = get_proxmox_client()
            container_ids = client.get_container_ids()
            
            # Filter out ignored containers
            filtered_containers = [
                ctid for ctid in container_ids 
                if ctid and not is_ignored(ctid)
            ]
            logging.debug(f"Found containers via API: {filtered_containers}, ignored: {IGNORE_LXC}")
            return filtered_containers
            
        except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
            logging.warning(f"Proxmox API failed, falling back to command execution: {e}")
    
    # Fallback to command execution
    containers = run_command("pct list | awk 'NR>1 {print $1}'")
    if not containers:
        return []
        
    container_list = containers.splitlines()
    # Filter out ignored containers
    filtered_containers = [
        ctid for ctid in container_list 
        if ctid and not is_ignored(ctid)
    ]
    logging.debug(f"Found containers via command: {filtered_containers}, ignored: {IGNORE_LXC}")
    return filtered_containers

def is_ignored(ctid: str) -> bool:
    """Check if container should be ignored."""
    ignored = str(ctid) in IGNORE_LXC
    logging.debug(f"Container {ctid} is ignored: {ignored}")
    return ignored

def is_container_running(ctid: str) -> bool:
    """Check if a container is running.

    Args:
        ctid: The container ID.

    Returns:
        True if the container is running, False otherwise.
    """
    # Try Proxmox API first if available
    if PROXMOX_API_AVAILABLE and config.get('DEFAULT', {}).get('use_proxmox_api', True):
        try:
            client = get_proxmox_client()
            running = client.is_container_running(ctid)
            logging.debug(f"Container {ctid} running status via API: {running}")
            return running
            
        except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
            logging.warning(f"Proxmox API failed for container {ctid}, falling back to command: {e}")
    
    # Fallback to command execution
    status = run_command(f"pct status {ctid}")
    running = bool(status and "status: running" in status.lower())
    logging.debug(f"Container {ctid} running status via command: {running}")
    return running


def backup_container_settings(ctid: str, settings: Optional[Dict[str, Any]] = None) -> None:
    """Backup container configuration to JSON file.

    Args:
        ctid: The container ID.
        settings: The container settings to backup. If None, fetch from API.
    """
    try:
        # If no settings provided, fetch current configuration
        if settings is None:
            settings = get_container_current_config(ctid)
            if not settings:
                logging.warning(f"Could not fetch configuration for container {ctid}")
                return
        
        os.makedirs(BACKUP_DIR, exist_ok=True)
        backup_file = os.path.join(BACKUP_DIR, f"{ctid}_backup.json")
        with lock:
            with open(backup_file, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=2)
        logging.debug("Backup saved for container %s: %s", ctid, settings)
    except Exception as e:  # pylint: disable=broad-except
        logging.error("Failed to backup settings for %s: %s", ctid, str(e))


def load_backup_settings(ctid: str) -> Optional[Dict[str, Any]]:
    """Load container configuration from a backup JSON file.

    Args:
        ctid: The container ID.

    Returns:
        The loaded container settings, or None if no backup is found.
    """
    try:
        backup_file = os.path.join(BACKUP_DIR, f"{ctid}_backup.json")
        if os.path.exists(backup_file):
            with lock:
                with open(backup_file, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
            logging.debug("Loaded backup for container %s: %s", ctid, settings)
            return settings
        logging.warning("No backup found for container %s", ctid)
        return None
    except Exception as e:  # pylint: disable=broad-except
        logging.error("Failed to load backup for %s: %s", ctid, str(e))
        return None


def rollback_container_settings(ctid: str) -> None:
    """Restore container settings from backup.

    Args:
        ctid: The container ID.
    """
    settings = load_backup_settings(ctid)
    if settings:
        logging.info("Rolling back container %s to backup settings", ctid)
        
        # Try Proxmox API first if available
        if PROXMOX_API_AVAILABLE and config.get('DEFAULT', {}).get('use_proxmox_api', True):
            try:
                client = get_proxmox_client()
                update_params = {
                    'cores': settings['cores'],
                    'memory': settings['memory']
                }
                
                success = client.update_container_config(ctid, **update_params)
                if success:
                    logging.info(f"Rolled back container {ctid} via API")
                    return
                    
            except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
                logging.warning(f"Proxmox API rollback failed for container {ctid}, falling back to command: {e}")
        
        # Fallback to command execution
        run_command(f"pct set {ctid} -cores {settings['cores']}")
        run_command(f"pct set {ctid} -memory {settings['memory']}")


def log_json_event(ctid: str, action: str, resource_change: str) -> None:
    """Log container change events in JSON format.

    Args:
        ctid: The container ID.
        action: The action that was performed.
        resource_change: Details of the resource change.
    """
    log_data = {
        "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "proxmox_host": PROXMOX_HOSTNAME,
        "container_id": ctid,
        "action": action,
        "change": resource_change,
    }
    with lock:
        with open(LOG_FILE.replace('.log', '.json'), 'a', encoding='utf-8') as json_log_file:
            json_log_file.write(json.dumps(log_data) + '\n')
    logging.info("Logged event for container %s: %s - %s", ctid, action, resource_change)


def get_total_cores() -> int:
    """Calculate available CPU cores after reserving percentage.

    Returns:
        The available number of CPU cores.
    """
    total_cores = int(run_command("nproc") or 0)
    reserved_cores = max(1, int(total_cores * int(get_config_value('DEFAULT', 'reserve_cpu_percent', 10)) / 100))
    available_cores = total_cores - reserved_cores
    logging.debug(
        "Total cores: %d, Reserved: %d, Available: %d",
        total_cores,
        reserved_cores,
        available_cores,
    )
    return available_cores


def get_total_memory() -> int:
    """Calculate available memory after reserving a fixed amount.

    Returns:
        The available memory in MB.
    """
    try:
        command_output = run_command("free -m | awk '/^Mem:/ {print $2}'")
        total_memory = int(command_output.strip()) if command_output else 0
    except (ValueError, subprocess.CalledProcessError) as e:
        logging.error("Failed to get total memory: %s", str(e))
        total_memory = 0

    available_memory = max(0, total_memory - int(get_config_value('DEFAULT', 'reserve_memory_mb', 2048)))
    logging.debug(
        "Total memory: %dMB, Reserved: %dMB, Available: %dMB",
        total_memory,
        int(get_config_value('DEFAULT', 'reserve_memory_mb', 2048)),
        available_memory,
    )
    return available_memory


def get_container_current_config(ctid: str) -> Optional[Dict[str, Any]]:
    """Get current container configuration.
    
    Args:
        ctid: The container ID.
        
    Returns:
        Container configuration dictionary or None if failed.
    """
    # Try Proxmox API first if available
    if PROXMOX_API_AVAILABLE and config.get('DEFAULT', {}).get('use_proxmox_api', True):
        try:
            client = get_proxmox_client()
            container_config = client.get_container_config(ctid)
            
            # Extract relevant fields for backward compatibility
            settings = {
                'cores': container_config.get('cores', 1),
                'memory': container_config.get('memory', 512),
                'full_config': container_config  # Store full config for future use
            }
            
            logging.debug(f"Retrieved config for container {ctid} via API")
            return settings
            
        except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
            logging.warning(f"Proxmox API failed for container {ctid} config, falling back to command: {e}")
    
    # Fallback to command execution
    try:
        cores_output = run_command(f"pct config {ctid} | grep cores | awk '{{print $2}}'")
        memory_output = run_command(f"pct config {ctid} | grep memory | awk '{{print $2}}'")
        
        cores = int(cores_output) if cores_output else 1
        memory = int(memory_output) if memory_output else 512
        
        settings = {
            'cores': cores,
            'memory': memory
        }
        
        logging.debug(f"Retrieved config for container {ctid} via command")
        return settings
        
    except Exception as e:
        logging.error(f"Failed to get config for container {ctid}: {e}")
        return None

def get_cpu_usage(ctid: str) -> float:
    """Get container CPU usage using multiple fallback methods.

    Args:
        ctid: The container ID.

    Returns:
        The CPU usage as a float percentage (0.0 - 100.0).
    """
    # Try Proxmox API RRD data first if available
    if PROXMOX_API_AVAILABLE and config.get('DEFAULT', {}).get('use_proxmox_api', True):
        try:
            client = get_proxmox_client()
            rrd_data = client.get_container_rrd_data(ctid, timeframe='hour')
            
            if rrd_data and len(rrd_data) > 0:
                # Get the most recent data point
                latest_data = rrd_data[-1]
                
                # Calculate CPU usage percentage
                cpu_usage = latest_data.get('cpu', 0.0)
                if isinstance(cpu_usage, (int, float)):
                    # RRD data is typically in decimal format (0.0 - 1.0)
                    cpu_percentage = cpu_usage * 100 if cpu_usage <= 1.0 else cpu_usage
                    cpu_percentage = round(max(min(cpu_percentage, 100.0), 0.0), 2)
                    
                    logging.info("CPU usage for %s via API RRD: %.2f%%", ctid, cpu_percentage)
                    return cpu_percentage
                    
        except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
            logging.warning(f"Proxmox API RRD failed for container {ctid}, falling back to internal methods: {e}")
    
    # Fallback to internal monitoring methods
    def loadavg_method(ctid: str) -> float:
        """Calculate CPU usage using load average from within the container."""
        try:
            # Use pct exec to run commands inside the container
            loadavg_output = run_command(f"pct exec {ctid} -- cat /proc/loadavg")
            if not loadavg_output:
                raise RuntimeError("Failed to get loadavg")
                
            loadavg = float(loadavg_output.split()[0])
            # Get number of CPUs allocated to the container
            num_cpus = int(run_command(f"pct config {ctid} | grep cores | awk '{{print $2}}'") or 1)
            
            return round(min((loadavg / num_cpus) * 100, 100.0), 2)
        except Exception as e:
            raise RuntimeError(f"Loadavg method failed: {str(e)}") from e

    def proc_stat_method(ctid: str) -> float:
        """Calculate CPU usage using /proc/stat from within the container."""
        try:
            # Get initial CPU stats with better parsing
            cmd1 = f"pct exec {ctid} -- cat /proc/stat | grep '^cpu '"
            initial = run_command(cmd1)
            if not initial:
                raise RuntimeError("Failed to get initial CPU stats")
                
            initial_values = list(map(int, initial.split()[1:]))
            initial_idle = initial_values[3] + initial_values[4]  # idle + iowait
            initial_total = sum(initial_values)

            # Wait for a short period
            time.sleep(1)

            # Get current CPU stats
            cmd2 = f"pct exec {ctid} -- cat /proc/stat | grep '^cpu '"
            current = run_command(cmd2)
            if not current:
                raise RuntimeError("Failed to get current CPU stats")
                
            current_values = list(map(int, current.split()[1:]))
            current_idle = current_values[3] + current_values[4]  # idle + iowait
            current_total = sum(current_values)

            delta_idle = current_idle - initial_idle
            delta_total = current_total - initial_total

            if delta_total <= 0:
                return 0.0

            cpu_usage = ((delta_total - delta_idle) / delta_total) * 100
            return round(max(min(cpu_usage, 100.0), 0.0), 2)
        except Exception as e:
            raise RuntimeError(f"Proc stat method failed: {str(e)}") from e

    # Try each method in order
    methods = [
        ("Proc Stat", proc_stat_method),
        ("Load Average", loadavg_method)
    ]

    for method_name, method in methods:
        try:
            cpu = method(ctid)
            if cpu is not None and cpu >= 0.0:
                logging.info("CPU usage for %s using %s: %.2f%%", ctid, method_name, cpu)
                return cpu
        except Exception as e:
            logging.warning("%s failed for %s: %s", method_name, ctid, str(e))

    logging.error("All CPU usage methods failed for %s. Using 0.0", ctid)
    return 0.0


def get_memory_usage(ctid: str) -> float:
    """Get container memory usage percentage.

    Args:
        ctid: The container ID.

    Returns:
        The memory usage as a float percentage (0.0 - 100.0).
    """
    # Try Proxmox API RRD data first if available
    if PROXMOX_API_AVAILABLE and config.get('DEFAULT', {}).get('use_proxmox_api', True):
        try:
            client = get_proxmox_client()
            rrd_data = client.get_container_rrd_data(ctid, timeframe='hour')
            
            if rrd_data and len(rrd_data) > 0:
                # Get the most recent data point
                latest_data = rrd_data[-1]
                
                # Calculate memory usage percentage
                mem_used = latest_data.get('mem', 0)
                mem_max = latest_data.get('maxmem', 1)  # Avoid division by zero
                
                if isinstance(mem_used, (int, float)) and isinstance(mem_max, (int, float)) and mem_max > 0:
                    mem_percentage = (mem_used / mem_max) * 100
                    mem_percentage = round(max(min(mem_percentage, 100.0), 0.0), 2)
                    
                    logging.info("Memory usage for %s via API RRD: %.2f%%", ctid, mem_percentage)
                    return mem_percentage
                    
        except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
            logging.warning(f"Proxmox API RRD failed for container {ctid}, falling back to internal method: {e}")
    
    # Fallback to internal monitoring method
    mem_info = run_command(
        f"pct exec {ctid} -- awk '/MemTotal/ {{t=$2}} /MemAvailable/ {{a=$2}} "
        f"END {{print t, t-a}}' /proc/meminfo"
    )
    if mem_info:
        try:
            total, used = map(int, mem_info.split())
            mem_usage = (used * 100) / total
            logging.info("Memory usage for %s: %.2f%%", ctid, mem_usage)
            return mem_usage
        except ValueError:
            logging.error("Failed to parse memory info for %s: '%s'", ctid, mem_info)
    logging.error("Failed to get memory usage for %s", ctid)
    return 0.0


def get_container_data(ctid: str) -> Optional[Dict[str, Any]]:
    """Collect container resource usage data.

    Args:
        ctid: The container ID.

    Returns:
        A dictionary containing container resource data or None if not available.
    """
    if is_ignored(ctid) or not is_container_running(ctid):
        return None

    logging.debug("Collecting data for container %s", ctid)
    try:
        # Get current configuration (API or command fallback)
        config_data = get_container_current_config(ctid)
        if not config_data:
            logging.error(f"Failed to get configuration for container {ctid}")
            return None
        
        cores = config_data.get('cores', 1)
        memory = config_data.get('memory', 512)
        
        # Backup the configuration
        backup_container_settings(ctid, config_data)
        
        return {
            "cpu": get_cpu_usage(ctid),
            "mem": get_memory_usage(ctid),
            "initial_cores": cores,
            "initial_memory": memory,
        }
    except Exception as e:  # pylint: disable=broad-except
        logging.error("Error collecting data for %s: %s", ctid, str(e))
        return None


def collect_data_for_container(ctid: str) -> Optional[Dict[str, Dict[str, Any]]]:
    """Collect data for a single container."""
    data = get_container_data(ctid)
    if data:
        logging.debug("Data collected for container %s: %s", ctid, data)
        return {ctid: data}
    return None

def collect_container_data() -> Dict[str, Dict[str, Any]]:
    """Collect resource usage data for all containers."""
    containers: Dict[str, Dict[str, Any]] = {}
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(collect_data_for_container, ctid): ctid
            for ctid in get_containers()
            if not is_ignored(ctid)
        }
        
        for future in as_completed(futures):
            ctid = futures[future]
            try:
                result = future.result()
                if result:
                    containers.update(result)
                    # Apply tier settings
                    if ctid in LXC_TIER_ASSOCIATIONS:
                        tier_config = LXC_TIER_ASSOCIATIONS[ctid]
                        containers[ctid].update(tier_config)
                        logging.info(f"Applied tier settings for container {ctid} from tier {tier_config.get('tier_name', 'unknown')}")
            except Exception as e:
                logging.error(f"Error collecting data for container {ctid}: {e}")
    
    logging.info("Collected data for containers: %s", containers)
    return containers


def prioritize_containers(containers: Dict[str, Dict[str, Any]]) -> List[Tuple[str, Dict[str, Any]]]:
    """Sort containers by resource usage priority.

    Args:
        containers: A dictionary of container resource data.

    Returns:
        A sorted list of container IDs and their data.
    """
    if not containers:
        logging.info("No containers to prioritize.")
        return []

    try:
        priorities = sorted(
            containers.items(),
            key=lambda item: (item[1]['cpu'], item[1]['mem']),
            reverse=True,
        )
        logging.debug("Container priorities: %s", priorities)
        return priorities
    except Exception as e:  # pylint: disable=broad-except
        logging.error("Error prioritizing containers: %s", str(e))
        return []


def get_container_config(ctid: str) -> Dict[str, Any]:
    """Get container tier configuration.

    Args:
        ctid: The container ID.

    Returns:
        The container's tier configuration.
    """
    config = LXC_TIER_ASSOCIATIONS.get(ctid, config)
    logging.debug("Configuration for container %s: %s", ctid, config)
    return config


def generate_unique_snapshot_name(base_name: str) -> str:
    """Generate timestamped snapshot name.

    Args:
        base_name: Base name for the snapshot.

    Returns:
        A unique snapshot name.
    """
    snapshot_name = f"{base_name}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logging.debug("Generated unique snapshot name: %s", snapshot_name)
    return snapshot_name


def generate_cloned_hostname(base_name: str, clone_number: int) -> str:
    """Generate unique hostname for cloned container.

    Args:
        base_name: Base name for the cloned container.
        clone_number: The clone number.

    Returns:
        A unique hostname for the cloned container.
    """
    hostname = f"{base_name}-cloned-{clone_number}"
    logging.debug("Generated cloned hostname: %s", hostname)
    return hostname

def scale_container_resources(ctid: str, cores: Optional[int] = None, memory: Optional[int] = None) -> bool:
    """Scale container resources (CPU cores and/or memory).
    
    Args:
        ctid: The container ID.
        cores: New number of CPU cores (optional).
        memory: New memory allocation in MB (optional).
        
    Returns:
        True if scaling was successful, False otherwise.
    """
    if not cores and not memory:
        logging.warning(f"No scaling parameters provided for container {ctid}")
        return False
    
    # Try Proxmox API first if available
    if PROXMOX_API_AVAILABLE and config.get('DEFAULT', {}).get('use_proxmox_api', True):
        try:
            client = get_proxmox_client()
            update_params = {}
            
            if cores is not None:
                update_params['cores'] = cores
            if memory is not None:
                update_params['memory'] = memory
            
            success = client.update_container_config(ctid, **update_params)
            if success:
                logging.info(f"Scaled container {ctid} via API: {update_params}")
                return True
                
        except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
            logging.warning(f"Proxmox API scaling failed for container {ctid}, falling back to command: {e}")
    
    # Fallback to command execution
    try:
        if cores is not None:
            result = run_command(f"pct set {ctid} -cores {cores}")
            if not result:
                logging.error(f"Failed to set cores for container {ctid}")
                return False
        
        if memory is not None:
            result = run_command(f"pct set {ctid} -memory {memory}")
            if not result:
                logging.error(f"Failed to set memory for container {ctid}")
                return False
        
        logging.info(f"Scaled container {ctid} via command: cores={cores}, memory={memory}")
        return True
        
    except Exception as e:
        logging.error(f"Failed to scale container {ctid}: {e}")
        return False


def clone_container_api(source_ctid: str, new_ctid: str, hostname: Optional[str] = None) -> bool:
    """Clone a container using Proxmox API or command fallback.
    
    Args:
        source_ctid: Source container ID.
        new_ctid: New container ID.
        hostname: Hostname for the new container (optional).
        
    Returns:
        True if cloning was successful, False otherwise.
    """
    # Try Proxmox API first if available
    if PROXMOX_API_AVAILABLE and config.get('DEFAULT', {}).get('use_proxmox_api', True):
        try:
            client = get_proxmox_client()
            success = client.clone_container(source_ctid, new_ctid, hostname=hostname)
            if success:
                logging.info(f"Cloned container {source_ctid} to {new_ctid} via API")
                return True
                
        except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
            logging.warning(f"Proxmox API cloning failed, falling back to command: {e}")
    
    # Fallback to command execution
    try:
        clone_cmd = f"pct clone {source_ctid} {new_ctid}"
        if hostname:
            clone_cmd += f" --hostname {hostname}"
        
        result = run_command(clone_cmd)
        if result:
            logging.info(f"Cloned container {source_ctid} to {new_ctid} via command")
            return True
        else:
            logging.error(f"Failed to clone container {source_ctid} to {new_ctid}")
            return False
            
    except Exception as e:
        logging.error(f"Failed to clone container {source_ctid}: {e}")
        return False


def start_container_api(ctid: str) -> bool:
    """Start a container using Proxmox API or command fallback.
    
    Args:
        ctid: Container ID.
        
    Returns:
        True if start was successful, False otherwise.
    """
    # Try Proxmox API first if available
    if PROXMOX_API_AVAILABLE and config.get('DEFAULT', {}).get('use_proxmox_api', True):
        try:
            client = get_proxmox_client()
            success = client.start_container(ctid)
            if success:
                logging.info(f"Started container {ctid} via API")
                return True
                
        except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
            logging.warning(f"Proxmox API start failed for container {ctid}, falling back to command: {e}")
    
    # Fallback to command execution
    try:
        result = run_command(f"pct start {ctid}")
        if result is not None:  # Command executed (result can be empty string)
            logging.info(f"Started container {ctid} via command")
            return True
        else:
            logging.error(f"Failed to start container {ctid}")
            return False
            
    except Exception as e:
        logging.error(f"Failed to start container {ctid}: {e}")
        return False


def stop_container_api(ctid: str) -> bool:
    """Stop a container using Proxmox API or command fallback.
    
    Args:
        ctid: Container ID.
        
    Returns:
        True if stop was successful, False otherwise.
    """
    # Try Proxmox API first if available
    if PROXMOX_API_AVAILABLE and config.get('DEFAULT', {}).get('use_proxmox_api', True):
        try:
            client = get_proxmox_client()
            success = client.stop_container(ctid)
            if success:
                logging.info(f"Stopped container {ctid} via API")
                return True
                
        except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
            logging.warning(f"Proxmox API stop failed for container {ctid}, falling back to command: {e}")
    
    # Fallback to command execution
    try:
        result = run_command(f"pct stop {ctid}")
        if result is not None:  # Command executed (result can be empty string)
            logging.info(f"Stopped container {ctid} via command")
            return True
        else:
            logging.error(f"Failed to stop container {ctid}")
            return False
            
    except Exception as e:
        logging.error(f"Failed to stop container {ctid}: {e}")
        return False


def get_node_resource_usage() -> Dict[str, Any]:
    """Get node resource usage information.
    
    Returns:
        Dictionary containing node resource usage data.
    """
    # Try Proxmox API first if available
    if PROXMOX_API_AVAILABLE and config.get('DEFAULT', {}).get('use_proxmox_api', True):
        try:
            client = get_proxmox_client()
            node_status = client.get_node_status()
            
            # Extract relevant resource information
            cpu_usage = node_status.get('cpu', 0.0) * 100  # Convert to percentage
            memory_used = node_status.get('memory', {}).get('used', 0)
            memory_total = node_status.get('memory', {}).get('total', 1)
            memory_usage = (memory_used / memory_total) * 100 if memory_total > 0 else 0.0
            
            resource_data = {
                'cpu_usage': round(cpu_usage, 2),
                'memory_usage': round(memory_usage, 2),
                'memory_used_mb': memory_used // (1024 * 1024),  # Convert to MB
                'memory_total_mb': memory_total // (1024 * 1024),  # Convert to MB
                'cpu_cores': node_status.get('cpuinfo', {}).get('cpus', 1),
                'uptime': node_status.get('uptime', 0)
            }
            
            logging.debug(f"Retrieved node resource usage via API: {resource_data}")
            return resource_data
            
        except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
            logging.warning(f"Proxmox API node status failed, falling back to command: {e}")
    
    # Fallback to command execution
    try:
        # Get CPU cores
        total_cores = int(run_command("nproc") or 1)
        
        # Get memory information
        memory_output = run_command("free -m | awk '/^Mem:/ {print $2, $3}'")
        if memory_output:
            memory_parts = memory_output.split()
            memory_total_mb = int(memory_parts[0])
            memory_used_mb = int(memory_parts[1])
            memory_usage = (memory_used_mb / memory_total_mb) * 100
        else:
            memory_total_mb = memory_used_mb = memory_usage = 0
        
        # Get load average as CPU usage approximation
        load_output = run_command("cat /proc/loadavg | awk '{print $1}'")
        if load_output:
            load_avg = float(load_output)
            cpu_usage = min((load_avg / total_cores) * 100, 100.0)
        else:
            cpu_usage = 0.0
        
        resource_data = {
            'cpu_usage': round(cpu_usage, 2),
            'memory_usage': round(memory_usage, 2),
            'memory_used_mb': memory_used_mb,
            'memory_total_mb': memory_total_mb,
            'cpu_cores': total_cores,
            'uptime': 0  # Not easily available via command
        }
        
        logging.debug(f"Retrieved node resource usage via command: {resource_data}")
        return resource_data
        
    except Exception as e:
        logging.error(f"Failed to get node resource usage: {e}")
        return {
            'cpu_usage': 0.0,
            'memory_usage': 0.0,
            'memory_used_mb': 0,
            'memory_total_mb': 1,
            'cpu_cores': 1,
            'uptime': 0
        }


import atexit
atexit.register(close_ssh_client)

