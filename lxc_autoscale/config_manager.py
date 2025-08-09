"""Centralized configuration management for the LXC autoscaling system."""

import logging
import os
import sys
from socket import gethostname
from typing import Any, Dict, List, Optional, Set, Union

import yaml

from constants import (
    DEFAULT_CONFIG_FILE, DEFAULT_LOG_FILE, DEFAULT_LOCK_FILE, DEFAULT_BACKUP_DIR,
    DEFAULT_POLL_INTERVAL, DEFAULT_RESERVE_CPU_PERCENT, DEFAULT_RESERVE_MEMORY_MB,
    DEFAULT_OFF_PEAK_START, DEFAULT_OFF_PEAK_END, DEFAULT_CPU_UPPER_THRESHOLD,
    DEFAULT_CPU_LOWER_THRESHOLD, DEFAULT_MEMORY_UPPER_THRESHOLD, DEFAULT_MEMORY_LOWER_THRESHOLD,
    DEFAULT_MIN_CORES, DEFAULT_MAX_CORES, DEFAULT_MIN_MEMORY, DEFAULT_CORE_MIN_INCREMENT,
    DEFAULT_CORE_MAX_INCREMENT, DEFAULT_MEMORY_MIN_INCREMENT, DEFAULT_MIN_DECREASE_CHUNK,
    DEFAULT_CPU_SCALE_DIVISOR, DEFAULT_MEMORY_SCALE_FACTOR, DEFAULT_TIMEOUT_EXTENDED,
    BEHAVIOR_NORMAL
)
from error_handler import ConfigurationError, ErrorHandler, handle_configuration_errors


class ConfigManager:
    """Centralized configuration manager."""
    
    def __init__(self, config_file: str = DEFAULT_CONFIG_FILE):
        """Initialize configuration manager.
        
        Args:
            config_file: Path to the configuration file
        """
        self.config_file = config_file
        self._config: Dict[str, Any] = {}
        self._defaults: Dict[str, Any] = {}
        self._tier_configurations: Dict[str, Dict[str, Any]] = {}
        self._horizontal_scaling_groups: Dict[str, Dict[str, Any]] = {}
        self._ignore_lxc: Set[str] = set()
        
        self._initialize_defaults()
        self._load_configuration()
        self._validate_configuration()
    
    def _initialize_defaults(self) -> None:
        """Initialize default configuration values."""
        self._defaults = {
            'poll_interval': DEFAULT_POLL_INTERVAL,
            'energy_mode': False,
            'behaviour': BEHAVIOR_NORMAL,
            'reserve_cpu_percent': DEFAULT_RESERVE_CPU_PERCENT,
            'reserve_memory_mb': DEFAULT_RESERVE_MEMORY_MB,
            'off_peak_start': DEFAULT_OFF_PEAK_START,
            'off_peak_end': DEFAULT_OFF_PEAK_END,
            'cpu_upper_threshold': DEFAULT_CPU_UPPER_THRESHOLD,
            'cpu_lower_threshold': DEFAULT_CPU_LOWER_THRESHOLD,
            'memory_upper_threshold': DEFAULT_MEMORY_UPPER_THRESHOLD,
            'memory_lower_threshold': DEFAULT_MEMORY_LOWER_THRESHOLD,
            'min_cores': DEFAULT_MIN_CORES,
            'max_cores': DEFAULT_MAX_CORES,
            'min_memory': DEFAULT_MIN_MEMORY,
            'core_min_increment': DEFAULT_CORE_MIN_INCREMENT,
            'core_max_increment': DEFAULT_CORE_MAX_INCREMENT,
            'memory_min_increment': DEFAULT_MEMORY_MIN_INCREMENT,
            'min_decrease_chunk': DEFAULT_MIN_DECREASE_CHUNK,
            'cpu_scale_divisor': DEFAULT_CPU_SCALE_DIVISOR,
            'memory_scale_factor': DEFAULT_MEMORY_SCALE_FACTOR,
            'timeout_extended': DEFAULT_TIMEOUT_EXTENDED,
            'log_file': DEFAULT_LOG_FILE,
            'lock_file': DEFAULT_LOCK_FILE,
            'backup_dir': DEFAULT_BACKUP_DIR,
            'ignore_lxc': []
        }
    
    @handle_configuration_errors
    def _load_configuration(self) -> None:
        """Load configuration from YAML file."""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                self._config = yaml.safe_load(f) or {}
            logging.info(f"Configuration loaded from {self.config_file}")
        except FileNotFoundError:
            logging.warning(f"Config file not found at {self.config_file}, using defaults")
            self._config = {}
        except yaml.YAMLError as e:
            raise ConfigurationError(f"Error parsing config file: {e}")
        except Exception as e:
            raise ConfigurationError(f"Unexpected error loading config: {e}")
        
        # Update defaults with config file values
        default_section = self._config.get('DEFAULT', {})
        if isinstance(default_section, dict):
            self._defaults.update(default_section)
        
        # Load tier configurations
        self._load_tier_configurations()
        
        # Load horizontal scaling groups
        self._load_horizontal_scaling_groups()
        
        # Load ignore list
        self._ignore_lxc = set(str(x) for x in self._defaults.get('ignore_lxc', []))
    
    def _load_tier_configurations(self) -> None:
        """Load and validate tier configurations."""
        self._tier_configurations = {}
        
        for section, values in self._config.items():
            if section.startswith('TIER_') and isinstance(values, dict):
                tier_name = section[5:]
                containers = values.get('lxc_containers', [])
                
                if not containers:
                    logging.warning(f"No containers defined for tier {tier_name}")
                    continue
                
                # Convert container IDs to strings for consistent comparison
                containers = [str(ctid) for ctid in containers]
                
                for ctid in containers:
                    tier_config = {
                        'cpu_upper_threshold': values.get('cpu_upper_threshold', self._defaults['cpu_upper_threshold']),
                        'cpu_lower_threshold': values.get('cpu_lower_threshold', self._defaults['cpu_lower_threshold']),
                        'memory_upper_threshold': values.get('memory_upper_threshold', self._defaults['memory_upper_threshold']),
                        'memory_lower_threshold': values.get('memory_lower_threshold', self._defaults['memory_lower_threshold']),
                        'min_cores': values.get('min_cores', self._defaults['min_cores']),
                        'max_cores': values.get('max_cores', self._defaults['max_cores']),
                        'min_memory': values.get('min_memory', self._defaults['min_memory']),
                        'core_min_increment': values.get('core_min_increment', self._defaults['core_min_increment']),
                        'core_max_increment': values.get('core_max_increment', self._defaults['core_max_increment']),
                        'memory_min_increment': values.get('memory_min_increment', self._defaults['memory_min_increment']),
                        'min_decrease_chunk': values.get('min_decrease_chunk', self._defaults['min_decrease_chunk']),
                        'tier_name': tier_name
                    }
                    
                    # Validate tier configuration
                    self._validate_tier_configuration(ctid, tier_config)
                    self._tier_configurations[ctid] = tier_config
                    
                logging.info(f"Loaded tier configuration '{tier_name}' for containers: {containers}")
    
    def _load_horizontal_scaling_groups(self) -> None:
        """Load horizontal scaling group configurations."""
        self._horizontal_scaling_groups = {}
        
        for section, group_config in self._config.items():
            if section.startswith('HORIZONTAL_SCALING_GROUP_') and isinstance(group_config, dict):
                lxc_containers = group_config.get('lxc_containers')
                if lxc_containers and isinstance(lxc_containers, list):
                    group_config['lxc_containers'] = set(map(str, lxc_containers))
                    self._horizontal_scaling_groups[section] = group_config
                    logging.info(f"Loaded horizontal scaling group: {section}")
                else:
                    logging.warning(f"Invalid or missing lxc_containers in {section}")
    
    @handle_configuration_errors
    def _validate_configuration(self) -> None:
        """Validate essential configuration values."""
        required_defaults = [
            'reserve_cpu_percent', 'reserve_memory_mb', 'off_peak_start', 'off_peak_end',
            'behaviour', 'cpu_upper_threshold', 'cpu_lower_threshold',
            'memory_upper_threshold', 'memory_lower_threshold'
        ]
        
        ErrorHandler.validate_required_config(self._defaults, required_defaults, "DEFAULTS")
        
        # Validate threshold ranges
        ErrorHandler.validate_threshold_ranges(
            self._defaults['cpu_lower_threshold'],
            self._defaults['cpu_upper_threshold'],
            "CPU"
        )
        
        ErrorHandler.validate_threshold_ranges(
            self._defaults['memory_lower_threshold'],
            self._defaults['memory_upper_threshold'],
            "Memory"
        )
        
        # Validate behavior mode
        valid_behaviors = [BEHAVIOR_NORMAL, BEHAVIOR_CONSERVATIVE, BEHAVIOR_AGGRESSIVE]
        if self._defaults['behaviour'] not in valid_behaviors:
            raise ConfigurationError(
                f"Invalid behavior mode: {self._defaults['behaviour']}. "
                f"Must be one of: {', '.join(valid_behaviors)}"
            )
    
    def _validate_tier_configuration(self, ctid: str, tier_config: Dict[str, Any]) -> None:
        """Validate tier configuration for a specific container.
        
        Args:
            ctid: Container ID
            tier_config: Tier configuration to validate
        
        Raises:
            ConfigurationError: If configuration is invalid
        """
        required_fields = [
            'cpu_upper_threshold', 'cpu_lower_threshold',
            'memory_upper_threshold', 'memory_lower_threshold',
            'min_cores', 'max_cores', 'min_memory'
        ]
        
        ErrorHandler.validate_required_config(tier_config, required_fields, f"TIER for container {ctid}")
        
        # Validate threshold ranges
        ErrorHandler.validate_threshold_ranges(
            tier_config['cpu_lower_threshold'],
            tier_config['cpu_upper_threshold'],
            f"CPU for container {ctid}"
        )
        
        ErrorHandler.validate_threshold_ranges(
            tier_config['memory_lower_threshold'],
            tier_config['memory_upper_threshold'],
            f"Memory for container {ctid}"
        )
        
        # Validate core limits
        if not (MIN_CORES_LIMIT <= tier_config['min_cores'] <= tier_config['max_cores']):
            raise ConfigurationError(
                f"Invalid core limits for container {ctid}: "
                f"min={tier_config['min_cores']}, max={tier_config['max_cores']}"
            )
        
        # Validate memory limits
        if tier_config['min_memory'] < MIN_MEMORY_LIMIT:
            raise ConfigurationError(
                f"Minimum memory for container {ctid} must be at least {MIN_MEMORY_LIMIT}MB"
            )
    
    def get_value(self, section: str, key: str, default: Any = None) -> Any:
        """Get configuration value with fallback to default.
        
        Args:
            section: Configuration section
            key: Configuration key
            default: Default value if not found
            
        Returns:
            Configuration value or default
        """
        return self._config.get(section, {}).get(key, self._defaults.get(key, default))
    
    def get_default(self, key: str, default: Any = None) -> Any:
        """Get default configuration value.
        
        Args:
            key: Configuration key
            default: Default value if not found
            
        Returns:
            Default configuration value
        """
        return self._defaults.get(key, default)
    
    def get_tier_config(self, ctid: str) -> Dict[str, Any]:
        """Get tier configuration for a container.
        
        Args:
            ctid: Container ID
            
        Returns:
            Tier configuration or defaults
        """
        return self._tier_configurations.get(str(ctid), self._defaults)
    
    def get_horizontal_scaling_groups(self) -> Dict[str, Dict[str, Any]]:
        """Get horizontal scaling group configurations.
        
        Returns:
            Dictionary of horizontal scaling group configurations
        """
        return self._horizontal_scaling_groups.copy()
    
    def is_ignored(self, ctid: str) -> bool:
        """Check if a container should be ignored.
        
        Args:
            ctid: Container ID
            
        Returns:
            True if container should be ignored
        """
        return str(ctid) in self._ignore_lxc
    
    def get_proxmox_hostname(self) -> str:
        """Get Proxmox hostname.
        
        Returns:
            Hostname of the Proxmox host
        """
        return gethostname()
    
    def reload(self) -> None:
        """Reload configuration from file."""
        logging.info("Reloading configuration...")
        self._initialize_defaults()
        self._load_configuration()
        self._validate_configuration()
        logging.info("Configuration reloaded successfully")


# Global configuration manager instance
config_manager = ConfigManager()

# Export commonly used constants for backward compatibility
BACKUP_DIR = config_manager.get_default('backup_dir')
IGNORE_LXC = config_manager._ignore_lxc
LOG_FILE = config_manager.get_default('log_file')
LXC_TIER_ASSOCIATIONS = config_manager._tier_configurations
PROXMOX_HOSTNAME = config_manager.get_proxmox_hostname()

# Export config manager methods for backward compatibility
config = config_manager._config
get_config_value = config_manager.get_value