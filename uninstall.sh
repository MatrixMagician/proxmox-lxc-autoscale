#!/bin/bash

set -euo pipefail  # Enable strict error handling

# Define the timestamp for backup and log filenames
readonly TIMESTAMP=$(date +"%Y%m%d%H%M%S")
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly LOGFILE="/var/log/lxc_uninstall_${TIMESTAMP}.log"

# Define directories and files
readonly BACKUP_DIR="/etc/lxc_autoscale/backups"
readonly CONFIG_FILE="/etc/lxc_autoscale/lxc_autoscale.yaml"
readonly INSTALL_DIRS=(
    "/usr/local/bin/lxc_autoscale"
    "/etc/lxc_autoscale"
    "/var/lib/lxc_autoscale"
    "/var/cache/lxc_autoscale"
    "/tmp/lxc_autoscale_cache"
    "/opt/lxc_autoscale_venv"
)
readonly LOG_FILES=(
    "/var/log/lxc_autoscale.log"
    "/var/log/lxc_autoscale.json"
    "/var/log/lxc_autoscale_performance.log"
    "/var/log/lxc_autoscale_memory.log"
)

# Define text styles and emojis using printf for better portability
readonly BOLD=$(tput bold 2>/dev/null || printf '')
readonly RESET=$(tput sgr0 2>/dev/null || printf '')
readonly GREEN=$(tput setaf 2 2>/dev/null || printf '')
readonly RED=$(tput setaf 1 2>/dev/null || printf '')
readonly YELLOW=$(tput setaf 3 2>/dev/null || printf '')
readonly CHECKMARK=$'\xe2\x9c\x85'
readonly CROSSMARK=$'\xe2\x9d\x8c'
readonly THANKS=$'\xf0\x9f\x99\x8f'
readonly URL=$'\xf0\x9f\x94\x97'

# Trap errors and cleanup
cleanup() {
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        log "ERROR" "Script failed with exit code: $exit_code"
        log "ERROR" "Check $LOGFILE for details"
    fi
    exit $exit_code
}
trap cleanup EXIT

# Enhanced logging function with severity levels
log() {
    local level="$1"
    local message="$2"
    local timestamp
    timestamp=$(date +"%Y-%m-%d %H:%M:%S")
    
    case $level in
        "INFO")
            printf "%s [%s%s%s] %s\n" "$timestamp" "${GREEN}" "$level" "${RESET}" "$message" | tee -a "$LOGFILE"
            ;;
        "WARN")
            printf "%s [%s%s%s] %s\n" "$timestamp" "${YELLOW}" "$level" "${RESET}" "$message" | tee -a "$LOGFILE"
            ;;
        "ERROR")
            printf "%s [%s%s%s] %s\n" "$timestamp" "${RED}" "$level" "${RESET}" "$message" | tee -a "$LOGFILE"
            ;;
    esac
}

# Check if running as root
check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        log "ERROR" "This script must be run as root"
        exit 1
    fi
}

# Backup configuration
backup_config() {
    if [ ! -f "$CONFIG_FILE" ]; then
        log "WARN" "Configuration file not found at $CONFIG_FILE"
        return 0
    fi

    mkdir -p "$BACKUP_DIR" || {
        log "ERROR" "Failed to create backup directory at $BACKUP_DIR"
        return 1
    }

    local backup_file="${BACKUP_DIR}/lxc_autoscale_backup_${TIMESTAMP}.yaml"
    if cp "$CONFIG_FILE" "$backup_file"; then
        log "INFO" "${CHECKMARK} Configuration backed up to $backup_file"
        return 0
    else
        log "ERROR" "${CROSSMARK} Failed to backup configuration"
        return 1
    fi
}

# Stop services and processes
stop_services() {
    # Kill processes
    if pgrep -f lxc_autoscale >/dev/null; then
        pkill -9 -f lxc_autoscale && \
            log "INFO" "${CHECKMARK} Killed LXC AutoScale processes" || \
            log "ERROR" "${CROSSMARK} Failed to kill processes"
    else
        log "INFO" "No running LXC AutoScale processes found"
    fi

    # Stop and disable service
    if systemctl is-active lxc_autoscale.service >/dev/null 2>&1; then
        systemctl stop lxc_autoscale.service && \
        systemctl disable lxc_autoscale.service && \
        log "INFO" "${CHECKMARK} Stopped and disabled service" || \
        log "ERROR" "${CROSSMARK} Failed to stop/disable service"
    else
        log "INFO" "Service not active or not found"
    fi
}

# Remove Python dependencies (optional)
remove_python_dependencies() {
    log "INFO" "Checking for Python dependencies to remove..."
    
    # Check if virtual environment exists
    if [ -d "/opt/lxc_autoscale_venv" ]; then
        log "INFO" "${CHECKMARK} Virtual environment found - all dependencies are contained within it."
        log "INFO" "Virtual environment will be removed automatically with installation directories."
        return 0
    fi
    
    # Fallback: check for legacy system-wide packages (from older installations)
    log "INFO" "Checking for legacy system-wide Python packages..."
    local legacy_deps=("proxmoxer" "aiohttp" "asyncssh" "psutil" "cryptography" "aiofiles")
    local removed=0
    
    for dep in "${legacy_deps[@]}"; do
        if python3 -c "import ${dep}" 2>/dev/null; then
            if pip3 uninstall -y "${dep}" 2>/dev/null; then
                log "INFO" "${CHECKMARK} Removed legacy Python package: ${dep}"
                ((removed++))
            else
                log "WARN" "Failed to remove legacy Python package: ${dep}"
            fi
        fi
    done
    
    if [ $removed -gt 0 ]; then
        log "INFO" "Removed $removed legacy Python dependencies"
    else
        log "INFO" "No legacy system-wide Python dependencies found"
    fi
}

# Remove installation files
remove_files() {
    # Remove service file
    rm -f /etc/systemd/system/lxc_autoscale.service && \
        log "INFO" "${CHECKMARK} Removed service file" || \
        log "WARN" "Service file not found or couldn't be removed"

    # Clean up Python cache files
    log "INFO" "Cleaning up Python cache files..."
    find /usr/local/bin/lxc_autoscale -name "*.pyc" -delete 2>/dev/null || true
    find /usr/local/bin/lxc_autoscale -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

    # Remove installation directories
    for dir in "${INSTALL_DIRS[@]}"; do
        if [ -d "$dir" ]; then
            rm -rf "$dir" && \
                log "INFO" "${CHECKMARK} Removed $dir" || \
                log "ERROR" "${CROSSMARK} Failed to remove $dir"
        else
            log "INFO" "Directory $dir not found"
        fi
    done

    # Remove log files
    for log_file in "${LOG_FILES[@]}"; do
        if [ -f "$log_file" ]; then
            rm -f "$log_file" && \
                log "INFO" "${CHECKMARK} Removed $log_file" || \
                log "ERROR" "${CROSSMARK} Failed to remove $log_file"
        fi
    done
    
    # Remove any temporary files
    rm -f /tmp/lxc_autoscale_*.tmp 2>/dev/null || true
    rm -f /tmp/memory_optimizer_*.log 2>/dev/null || true
}

# Prompt for Python dependencies removal
prompt_python_removal() {
    if [ "${1:-}" = "--remove-deps" ]; then
        return 0  # Auto-confirm if flag is provided
    fi
    
    printf "\n${YELLOW}Do you want to remove the Python virtual environment and all dependencies? [y/N]: ${RESET}"
    read -r response
    case $response in
        [yY][eE][sS]|[yY])
            return 0
            ;;
        *)
            log "INFO" "Keeping virtual environment and all Python dependencies intact"
            return 1
            ;;
    esac
}

# Main execution
main() {
    check_root
    log "INFO" "Starting LXC AutoScale uninstallation..."
    
    backup_config
    stop_services
    remove_files
    
    # Optionally remove Python dependencies
    if prompt_python_removal "$@"; then
        remove_python_dependencies
    fi
    
    systemctl daemon-reload  # Reload systemd after service removal

    log "INFO" "LXC AutoScale v3.0 Performance Edition uninstallation complete!"
    log "INFO" "${THANKS} ${BOLD}Thank you for using the Enhanced LXC AutoScale with Proxmox API!${RESET}"
    log "INFO" "All performance optimization files, Proxmox API components, virtual environment, caches, and monitoring data have been removed."
    log "INFO" "${URL} ${BOLD}Repository: https://github.com/MatrixMagician/proxmox-lxc-autoscale${RESET}"
}

main "$@"
