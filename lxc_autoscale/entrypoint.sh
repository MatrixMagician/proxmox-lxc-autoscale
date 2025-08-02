#!/bin/bash

# LXC AutoScale v3.0 Performance Edition Docker Entrypoint
# Supports both sync and async modes, Proxmox API integration, and performance monitoring

# Check if a user-defined configuration file is provided
if [[ -z "${USER_CONF_PATH}" ]]; then
  echo "No user-defined configuration file provided. Using default configuration."
  export CONFIG_PATH='/app/lxc_autoscale.yaml'
else
  echo "Using user-defined configuration file at ${USER_CONF_PATH}."
  export CONFIG_PATH="${USER_CONF_PATH}"
fi

# Ensure that the config file exists
if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "Error: Configuration file ${CONFIG_PATH} does not exist."
  exit 1
fi

# Debug output for the configuration file path
echo "Using configuration file: ${CONFIG_PATH}"

# Set connection variables from environment or YAML
# Support both API and SSH connectivity
if [[ -z "${PROXMOX_HOST}" ]]; then
  PROXMOX_HOST=$(yq eval '.DEFAULT.proxmox_host' "${CONFIG_PATH}" 2>/dev/null || yq eval '.DEFAULT.proxmox_api_host' "${CONFIG_PATH}" 2>/dev/null)
  echo "Debug: PROXMOX_HOST read from YAML: ${PROXMOX_HOST}"
fi

# Check for API configuration first (preferred method)
PROXMOX_API_TOKEN=$(yq eval '.DEFAULT.proxmox_api_token_value' "${CONFIG_PATH}" 2>/dev/null)
USE_PROXMOX_API=$(yq eval '.DEFAULT.use_proxmox_api' "${CONFIG_PATH}" 2>/dev/null)

# Fall back to SSH if API not configured
if [[ -z "${PROXMOX_API_TOKEN}" || "${USE_PROXMOX_API}" != "true" ]]; then
  echo "API not configured or disabled, checking SSH credentials..."
  
  if [[ -z "${SSH_USER}" ]]; then
    SSH_USER=$(yq eval '.DEFAULT.ssh_user' "${CONFIG_PATH}")
    echo "Debug: SSH_USER read from YAML: ${SSH_USER}"
  fi
  
  if [[ -z "${SSH_PASS}" ]]; then
    SSH_PASS=$(yq eval '.DEFAULT.ssh_password' "${CONFIG_PATH}")
    echo "Debug: SSH_PASS read from YAML: ${SSH_PASS}"
  fi
else
  echo "Proxmox API configured, will use API for operations"
fi

# Verify connection configuration
if [[ -z "${PROXMOX_HOST}" ]]; then
  echo "Error: PROXMOX_HOST must be set via environment variables or in the YAML file."
  exit 1
fi

# Verify either API or SSH credentials are available
if [[ -z "${PROXMOX_API_TOKEN}" || "${USE_PROXMOX_API}" != "true" ]]; then
  if [[ -z "${SSH_USER}" || -z "${SSH_PASS}" ]]; then
    echo "Error: Either API credentials (proxmox_api_token_value) or SSH credentials (SSH_USER, SSH_PASS) must be configured."
    exit 1
  fi
fi

# Create required directories to ensure paths are writable
mkdir -p /var/log /var/lock /var/lib/lxc_autoscale/backups

# Function to test connection (API or SSH)
check_connection() {
  if [[ "${USE_PROXMOX_API}" == "true" && -n "${PROXMOX_API_TOKEN}" ]]; then
    echo "Testing Proxmox API connection..."
    # Test API connectivity using Python
    python3 -c "
import sys
sys.path.append('/app')
try:
    from proxmox_api_client import get_proxmox_client
    client = get_proxmox_client()
    containers = client.get_containers()
    print(f'API connection successful - found {len(containers)} containers')
except Exception as e:
    print(f'API connection failed: {e}')
    sys.exit(1)
" || {
      echo "Error: Unable to connect to Proxmox host ${PROXMOX_HOST} via API."
      exit 1
    }
  else
    echo "Testing SSH connection..."
    local ssh_test_command="echo 'SSH connection successful and command executed'"
    
    # Test SSH connection using sshpass
    sshpass -p "${SSH_PASS}" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "${SSH_USER}@${PROXMOX_HOST}" "${ssh_test_command}" >/dev/null 2>&1
  
    # Check if the SSH command was successful
    if [[ $? -ne 0 ]]; then
      echo "Error: Unable to connect to Proxmox host ${PROXMOX_HOST} via SSH or execute the test command."
      exit 1
    else
      echo "SSH connection to Proxmox host ${PROXMOX_HOST} successful, and test command executed correctly."
    fi
  fi
}

# Call the connection test function
check_connection

# Check if async mode is requested
ASYNC_MODE=${ASYNC_MODE:-false}
RUN_MODE=${RUN_MODE:-continuous}

# Start the Python application with the correct configuration path
echo "Starting the autoscaling application..."
echo "Configuration: ${CONFIG_PATH}"
echo "Async mode: ${ASYNC_MODE}"
echo "Run mode: ${RUN_MODE}"

if [[ "${ASYNC_MODE}" == "true" ]]; then
  echo "Starting in high-performance async mode..."
  if [[ "${RUN_MODE}" == "single-cycle" ]]; then
    python3 main_async.py --single-cycle
  else
    python3 main_async.py
  fi
else
  echo "Starting in standard synchronous mode..."
  python3 lxc_autoscale.py --config "${CONFIG_PATH}"
fi
