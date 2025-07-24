"""Backward compatibility module for the old config system.

This module provides backward compatibility by importing from the new config_manager.
New code should import directly from config_manager instead.
"""

import warnings
from config_manager import config_manager
from constants import *

# Issue deprecation warning
warnings.warn(
    "Importing from config.py is deprecated. Use config_manager instead.",
    DeprecationWarning,
    stacklevel=2
)

# Backward compatibility exports
CONFIG_FILE = DEFAULT_CONFIG_FILE
LOG_FILE = config_manager.get_default('log_file', DEFAULT_LOG_FILE)
BACKUP_DIR = config_manager.get_default('backup_dir', DEFAULT_BACKUP_DIR)
PROXMOX_HOSTNAME = config_manager.get_proxmox_hostname()

# Backward compatibility - use config_manager
config = config_manager._config

# Backward compatibility - use config_manager defaults
DEFAULTS = config_manager._defaults

def get_config_value(section: str, key: str, default: Any = None) -> Any:
    """Get configuration value with fallback to default."""
    return config_manager.get_value(section, key, default)

# Backward compatibility exports
IGNORE_LXC = set(str(x) for x in config_manager._defaults.get('ignore_lxc', []))
HORIZONTAL_SCALING_GROUPS = config_manager.get_horizontal_scaling_groups()
LXC_TIER_ASSOCIATIONS = config_manager._tier_configurations
CPU_SCALE_DIVISOR = config_manager.get_default('cpu_scale_divisor', DEFAULT_CPU_SCALE_DIVISOR)
MEMORY_SCALE_FACTOR = config_manager.get_default('memory_scale_factor', DEFAULT_MEMORY_SCALE_FACTOR)
TIMEOUT_EXTENDED = config_manager.get_default('timeout_extended', DEFAULT_TIMEOUT_EXTENDED)

# Backward compatibility - tier configuration loading is handled by config_manager
def load_tier_configurations():
    """Backward compatibility function - use config_manager instead."""
    return config_manager._tier_configurations

# Backward compatibility - validation is handled by config_manager
def validate_config():
    """Backward compatibility function - validation is handled by config_manager."""
    pass

# Backward compatibility exports - use config_manager for new code
LOCK_FILE = config_manager.get_default('lock_file', DEFAULT_LOCK_FILE)

__all__ = [
    'CONFIG_FILE',
    'DEFAULTS',
    'LOG_FILE',
    'LOCK_FILE',
    'BACKUP_DIR',
    'IGNORE_LXC',
    'PROXMOX_HOSTNAME',
    'CPU_SCALE_DIVISOR',
    'MEMORY_SCALE_FACTOR',
    'TIMEOUT_EXTENDED',
    'get_config_value',
    'HORIZONTAL_SCALING_GROUPS',
    'LXC_TIER_ASSOCIATIONS',
    'config',
    'load_tier_configurations',
    'validate_config',
]