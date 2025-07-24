"""Security validation utilities for the LXC autoscaling system."""

import os
import re
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from constants import (
    MIN_CORES_LIMIT, MIN_MEMORY_LIMIT, MAX_CPU_THRESHOLD, MIN_CPU_THRESHOLD,
    MAX_MEMORY_THRESHOLD, MIN_MEMORY_THRESHOLD
)
from error_handler import ValidationError


class SecurityValidator:
    """Provides security validation for inputs and operations."""
    
    # Allowed Proxmox commands
    ALLOWED_PROXMOX_COMMANDS = {
        'pct': ['config', 'set', 'start', 'stop', 'snapshot', 'clone', 'status'],
        'qm': ['config', 'set', 'start', 'stop', 'snapshot', 'clone', 'status'],
        'pvesh': ['get', 'set'],
        'pvesm': ['status'],
        'pvecm': ['status']
    }
    
    # Dangerous shell characters and sequences
    SHELL_INJECTION_PATTERNS = [
        r'[;&|`$()]',  # Shell metacharacters
        r'\\',         # Backslashes
        r'\$\(',       # Command substitution
        r'`',          # Backticks
        r'>>?',        # Redirection
        r'<<',         # Here documents
        r'\|\|?',      # Pipes and OR
        r'&&',         # AND
    ]
    
    # Valid container ID pattern (numbers only)
    CONTAINER_ID_PATTERN = re.compile(r'^\d+$')
    
    # Valid hostname pattern
    HOSTNAME_PATTERN = re.compile(r'^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$')
    
    # Valid snapshot name pattern
    SNAPSHOT_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,39}$')
    
    def __init__(self):
        """Initialize security validator."""
        self.logger = logging.getLogger(__name__)
    
    def validate_container_id(self, container_id: Union[str, int]) -> bool:
        """Validate container ID format and range.
        
        Args:
            container_id: Container ID to validate
            
        Returns:
            True if valid, False otherwise
        """
        try:
            ctid_str = str(container_id).strip()
            
            # Check pattern
            if not self.CONTAINER_ID_PATTERN.match(ctid_str):
                self.logger.warning(f"Invalid container ID format: {container_id}")
                return False
            
            # Check range (Proxmox container IDs are typically 100-999999)
            ctid_int = int(ctid_str)
            if not (100 <= ctid_int <= 999999):
                self.logger.warning(f"Container ID out of valid range: {container_id}")
                return False
            
            return True
            
        except (ValueError, TypeError):
            self.logger.warning(f"Invalid container ID type: {container_id}")
            return False
    
    def validate_hostname(self, hostname: str) -> bool:
        """Validate hostname format.
        
        Args:
            hostname: Hostname to validate
            
        Returns:
            True if valid, False otherwise
        """
        if not isinstance(hostname, str):
            return False
        
        hostname = hostname.strip()
        
        # Check length and pattern
        if not (1 <= len(hostname) <= 63) or not self.HOSTNAME_PATTERN.match(hostname):
            self.logger.warning(f"Invalid hostname format: {hostname}")
            return False
        
        return True
    
    def validate_snapshot_name(self, snapshot_name: str) -> bool:
        """Validate snapshot name format.
        
        Args:
            snapshot_name: Snapshot name to validate
            
        Returns:
            True if valid, False otherwise
        """
        if not isinstance(snapshot_name, str):
            return False
        
        snapshot_name = snapshot_name.strip()
        
        if not (1 <= len(snapshot_name) <= 40) or not self.SNAPSHOT_NAME_PATTERN.match(snapshot_name):
            self.logger.warning(f"Invalid snapshot name format: {snapshot_name}")
            return False
        
        return True
    
    def validate_command_safety(self, command: str) -> bool:
        """Validate that a command is safe to execute.
        
        Args:
            command: Command to validate
            
        Returns:
            True if safe, False otherwise
        """
        if not isinstance(command, str):
            self.logger.error("Command must be a string")
            return False
        
        command = command.strip()
        
        if not command:
            self.logger.error("Empty command not allowed")
            return False
        
        # Check for shell injection patterns
        for pattern in self.SHELL_INJECTION_PATTERNS:
            if re.search(pattern, command):
                self.logger.warning(f"Potentially unsafe command detected: {command}")
                return False
        
        # Validate command structure
        parts = command.split()
        if not parts:
            return False
        
        base_command = parts[0]
        
        # Check if it's an allowed Proxmox command
        if base_command in self.ALLOWED_PROXMOX_COMMANDS:
            if len(parts) > 1 and parts[1] not in self.ALLOWED_PROXMOX_COMMANDS[base_command]:
                self.logger.warning(f"Disallowed subcommand for {base_command}: {parts[1]}")
                return False
            return True
        
        # Allow some basic system commands
        allowed_system_commands = ['echo', 'cat', 'ls', 'ps', 'free', 'uptime']
        if base_command in allowed_system_commands:
            return True
        
        self.logger.warning(f"Command not in allowlist: {base_command}")
        return False
    
    def validate_resource_limits(
        self,
        cpu_cores: Optional[int] = None,
        memory_mb: Optional[int] = None,
        cpu_threshold: Optional[float] = None,
        memory_threshold: Optional[float] = None
    ) -> bool:
        """Validate resource limits and thresholds.
        
        Args:
            cpu_cores: Number of CPU cores
            memory_mb: Amount of memory in MB
            cpu_threshold: CPU threshold percentage
            memory_threshold: Memory threshold percentage
            
        Returns:
            True if all provided values are valid
        """
        # Validate CPU cores
        if cpu_cores is not None:
            if not isinstance(cpu_cores, int) or cpu_cores < MIN_CORES_LIMIT:
                self.logger.warning(f"Invalid CPU cores value: {cpu_cores}")
                return False
            
            # Reasonable upper limit
            if cpu_cores > 128:
                self.logger.warning(f"CPU cores value too high: {cpu_cores}")
                return False
        
        # Validate memory
        if memory_mb is not None:
            if not isinstance(memory_mb, int) or memory_mb < MIN_MEMORY_LIMIT:
                self.logger.warning(f"Invalid memory value: {memory_mb}")
                return False
            
            # Reasonable upper limit (1TB)
            if memory_mb > 1048576:
                self.logger.warning(f"Memory value too high: {memory_mb}")
                return False
        
        # Validate CPU threshold
        if cpu_threshold is not None:
            if not isinstance(cpu_threshold, (int, float)):
                return False
            if not (MIN_CPU_THRESHOLD <= cpu_threshold <= MAX_CPU_THRESHOLD):
                self.logger.warning(f"CPU threshold out of range: {cpu_threshold}")
                return False
        
        # Validate memory threshold
        if memory_threshold is not None:
            if not isinstance(memory_threshold, (int, float)):
                return False
            if not (MIN_MEMORY_THRESHOLD <= memory_threshold <= MAX_MEMORY_THRESHOLD):
                self.logger.warning(f"Memory threshold out of range: {memory_threshold}")
                return False
        
        return True
    
    def validate_file_path(self, file_path: str, allowed_directories: Optional[List[str]] = None) -> bool:
        """Validate file path for security.
        
        Args:
            file_path: File path to validate
            allowed_directories: List of allowed directory prefixes
            
        Returns:
            True if path is safe, False otherwise
        """
        if not isinstance(file_path, str):
            return False
        
        try:
            # Resolve path to prevent directory traversal
            resolved_path = Path(file_path).resolve()
            
            # Check for directory traversal attempts
            if '..' in file_path or file_path.startswith('/'):
                if not any(str(resolved_path).startswith(allowed_dir) for allowed_dir in (allowed_directories or [])):
                    self.logger.warning(f"Potentially unsafe file path: {file_path}")
                    return False
            
            # Check if path exists and is accessible
            if resolved_path.exists() and not os.access(resolved_path, os.R_OK):
                self.logger.warning(f"File not accessible: {file_path}")
                return False
            
            return True
            
        except (OSError, ValueError) as e:
            self.logger.warning(f"Invalid file path: {file_path} - {e}")
            return False
    
    def validate_configuration_values(self, config: Dict[str, Any]) -> List[str]:
        """Validate configuration values for security and correctness.
        
        Args:
            config: Configuration dictionary to validate
            
        Returns:
            List of validation error messages
        """
        errors = []
        
        # Validate numeric ranges
        numeric_validations = {
            'poll_interval': (30, 3600),  # 30 seconds to 1 hour
            'reserve_cpu_percent': (0, 50),  # 0% to 50%
            'reserve_memory_mb': (512, 16384),  # 512MB to 16GB
            'off_peak_start': (0, 23),
            'off_peak_end': (0, 23),
            'cpu_upper_threshold': (MIN_CPU_THRESHOLD, MAX_CPU_THRESHOLD),
            'cpu_lower_threshold': (MIN_CPU_THRESHOLD, MAX_CPU_THRESHOLD),
            'memory_upper_threshold': (MIN_MEMORY_THRESHOLD, MAX_MEMORY_THRESHOLD),
            'memory_lower_threshold': (MIN_MEMORY_THRESHOLD, MAX_MEMORY_THRESHOLD),
            'min_cores': (MIN_CORES_LIMIT, 128),
            'max_cores': (MIN_CORES_LIMIT, 128),
            'min_memory': (MIN_MEMORY_LIMIT, 1048576),
        }
        
        for key, (min_val, max_val) in numeric_validations.items():
            if key in config:
                value = config[key]
                if not isinstance(value, (int, float)) or not (min_val <= value <= max_val):
                    errors.append(f"Invalid {key}: {value} (must be between {min_val} and {max_val})")
        
        # Validate string values
        if 'behaviour' in config:
            valid_behaviors = ['normal', 'conservative', 'aggressive']
            if config['behaviour'] not in valid_behaviors:
                errors.append(f"Invalid behaviour: {config['behaviour']} (must be one of {valid_behaviors})")
        
        # Validate container IDs in ignore list
        if 'ignore_lxc' in config and isinstance(config['ignore_lxc'], list):
            for ctid in config['ignore_lxc']:
                if not self.validate_container_id(ctid):
                    errors.append(f"Invalid container ID in ignore list: {ctid}")
        
        # Validate SSH configuration
        ssh_keys = ['ssh_host', 'ssh_user', 'ssh_port']
        ssh_provided = any(key in config for key in ssh_keys)
        
        if ssh_provided:
            if 'ssh_port' in config:
                port = config['ssh_port']
                if not isinstance(port, int) or not (1 <= port <= 65535):
                    errors.append(f"Invalid SSH port: {port}")
            
            if 'ssh_user' in config:
                user = config['ssh_user']
                if not isinstance(user, str) or not user.strip():
                    errors.append("SSH user cannot be empty")
        
        return errors
    
    def sanitize_log_message(self, message: str) -> str:
        """Sanitize log message to prevent log injection.
        
        Args:
            message: Message to sanitize
            
        Returns:
            Sanitized message
        """
        if not isinstance(message, str):
            return str(message)
        
        # Remove or replace potentially dangerous characters
        sanitized = re.sub(r'[\r\n\t]', ' ', message)
        sanitized = re.sub(r'[^\x20-\x7E]', '?', sanitized)  # Replace non-printable chars
        
        # Limit length
        if len(sanitized) > 1000:
            sanitized = sanitized[:997] + '...'
        
        return sanitized
    
    def validate_network_config(self, network_config: Dict[str, Any]) -> List[str]:
        """Validate network configuration for security.
        
        Args:
            network_config: Network configuration to validate
            
        Returns:
            List of validation errors
        """
        errors = []
        
        # Validate network type
        network_type = network_config.get('clone_network_type')
        if network_type and network_type not in ['dhcp', 'static']:
            errors.append(f"Invalid network type: {network_type}")
        
        # Validate static IP range
        if network_type == 'static':
            static_range = network_config.get('static_ip_range', [])
            if not isinstance(static_range, list):
                errors.append("static_ip_range must be a list")
            else:
                ip_pattern = re.compile(r'^(\d{1,3}\.){3}\d{1,3}$')
                for ip in static_range:
                    if not isinstance(ip, str) or not ip_pattern.match(ip):
                        errors.append(f"Invalid IP address in static range: {ip}")
                    else:
                        # Validate IP octets
                        octets = ip.split('.')
                        if not all(0 <= int(octet) <= 255 for octet in octets):
                            errors.append(f"Invalid IP address octets: {ip}")
        
        return errors


# Global security validator instance
security_validator = SecurityValidator()