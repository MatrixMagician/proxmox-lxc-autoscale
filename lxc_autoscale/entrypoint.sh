#!/bin/bash

# LXC AutoScale v3.0 Performance Edition Docker Entrypoint
# Supports both sync and async modes, Proxmox API integration, and memory optimization

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
# API-only connectivity (SSH functionality removed)
if [[ -z "${PROXMOX_HOST}" ]]; then
  PROXMOX_HOST=$(yq eval '.DEFAULT.proxmox_host' "${CONFIG_PATH}" 2>/dev/null || yq eval '.DEFAULT.proxmox_api_host' "${CONFIG_PATH}" 2>/dev/null)
  echo "Debug: PROXMOX_HOST read from YAML: ${PROXMOX_HOST}"
fi

# Check if API credentials are provided
API_TOKEN_VALUE=$(yq eval '.DEFAULT.proxmox_api_token_value' "${CONFIG_PATH}")
API_PASSWORD=$(yq eval '.DEFAULT.proxmox_api_password' "${CONFIG_PATH}")

# Verify API credentials are available (password or token)
if [[ -z "${API_TOKEN_VALUE}" && -z "${API_PASSWORD}" ]]; then
  echo "Error: API credentials must be configured (proxmox_api_token_value or proxmox_api_password)."
  echo "SSH functionality has been removed - only Proxmox API authentication is supported."
  exit 1
else
  echo "API credentials configured, will use API for all operations."
fi

# Log configuration for debugging
echo "Debug: Configuration loaded from ${CONFIG_PATH}"
echo "Debug: PROXMOX_HOST=${PROXMOX_HOST}"
echo "Debug: API_TOKEN_VALUE=${API_TOKEN_VALUE:+[CONFIGURED]}"
echo "Debug: API_PASSWORD=${API_PASSWORD:+[CONFIGURED]}"

# Function to test API connection
test_connection() {
  echo "Testing Proxmox API connection..."
  
  # Simple API test - attempt to get version information
  if python3 -c "
import sys
sys.path.append('/app')
try:
    from lxc_autoscale.proxmox_api_client import get_proxmox_client
    client = get_proxmox_client()
    version = client._client.version.get()
    print(f'Successfully connected to Proxmox VE {version.get(\"version\", \"unknown\")}')
    exit(0)
except Exception as e:
    print(f'API connection failed: {e}')
    exit(1)
"; then
    echo "Proxmox API connection successful."
    return 0
  else
    echo "Error: Unable to connect to Proxmox host ${PROXMOX_HOST} via API."
    return 1
  fi
}

# Test the connection
if ! test_connection; then
  echo "Connection test failed. Please check your Proxmox API credentials and network connectivity."
  exit 1
fi

# Change to the working directory
cd /app || exit

# Set Python path to include the app directory
export PYTHONPATH="${PYTHONPATH}:/app:/app/lxc_autoscale"

# Determine execution mode based on environment variable
EXECUTION_MODE="${EXECUTION_MODE:-async}"
echo "Starting LXC AutoScale in ${EXECUTION_MODE} mode..."

if [[ "${EXECUTION_MODE}" == "async" ]]; then
  echo "Running in asynchronous high-performance mode"
  exec python3 -m lxc_autoscale.main_async
else
  echo "Running in synchronous mode (legacy)"
  # Note: You may need to create a sync entry point if needed
  exec python3 -m lxc_autoscale.main_async --sync-mode
fi