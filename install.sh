#!/bin/bash

# Log file
LOGFILE="lxc_autoscale_installer.log"

# Define text styles and emojis
BOLD=$(tput bold)
RESET=$(tput sgr0)
GREEN=$(tput setaf 2)
RED=$(tput setaf 1)
YELLOW=$(tput setaf 3)
BLUE=$(tput setaf 4)
CHECKMARK="\xE2\x9C\x85"  # ✔️
CROSSMARK="\xE2\x9D\x8C"  # ❌
CLOCK="\xE2\x8F\xB3"      # ⏳
ROCKET="\xF0\x9F\x9A\x80" # 🚀

# Log function
log() {
    local level="$1"
    local message="$2"
    local timestamp
    timestamp=$(date +"%Y-%m-%d %H:%M:%S")
    case $level in
        "INFO")
            echo -e "${timestamp} [${GREEN}${level}${RESET}] ${message}" | tee -a "$LOGFILE"
            ;;
        "ERROR")
            echo -e "${timestamp} [${RED}${level}${RESET}] ${message}" | tee -a "$LOGFILE"
            ;;
        "WARNING")
            echo -e "${timestamp} [${YELLOW}${level}${RESET}] ${message}" | tee -a "$LOGFILE"
            ;;
    esac
}

# ASCII Art Header with optional emoji
header() {
    echo -e "\n${BLUE}${BOLD}🎨 LXC AutoScale Installer v2.0${RESET}"
    echo "======================================="
    echo "Welcome to the LXC AutoScale cleanup and installation script!"
    echo ""
    echo "${GREEN}${BOLD}✨ New in v2.0 - Enterprise Features:${RESET}"
    echo "• 🔐 Enhanced Security - Input validation & command injection prevention"
    echo "• 🏗️  Modular Architecture - Maintainable and testable codebase"
    echo "• 📊 Structured Logging - JSON-formatted logs with performance metrics"
    echo "• 🔄 Retry Mechanisms - Automatic retry for transient failures"
    echo "• ⚡ Connection Pooling - Optimized SSH connection management"
    echo "• 🛡️  Centralized Error Handling - Comprehensive error management"
    echo "• 📈 Performance Monitoring - Real-time performance metrics"
    echo "• 🔧 Configuration Management - Validated configuration with type safety"
    echo "======================================="
    echo
}

# List of files to back up and then remove
files_to_backup_and_remove=(
    "/etc/lxc_autoscale/lxc_autoscale.conf"
    "/etc/lxc_autoscale/lxc_autoscale.yaml"
    "/etc/autoscaleapi.yaml"
)

# List of additional files and folders to remove without backup
files_and_folders_to_remove=(
    "/etc/lxc_autoscale_ml/lxc_autoscale_api.yaml"
    "/etc/lxc_autoscale_ml/lxc_autoscale_ml.yaml"
    "/etc/lxc_autoscale_ml/lxc_monitor.yaml"
    "/usr/local/bin/lxc_autoscale.py"
    "/usr/local/bin/lxc_monitor.py"
    "/usr/local/bin/lxc_autoscale_ml.py"
    "/usr/local/bin/autoscaleapi"
    "/var/log/lxc_autoscale.log"
    "/var/lib/lxc_autoscale/backups"
)

# Function to create a backup of specified files
backup_files() {
    local timestamp
    timestamp=$(date +"%Y%m%d%H%M%S")

    log "INFO" "Creating backups..."
    for file in "${files_to_backup_and_remove[@]}"; do
        if [[ -e "$file" ]]; then
            local backup_file="${file}_backup_${timestamp}"
            if cp "$file" "$backup_file"; then
                log "INFO" "Backed up $file to $backup_file"
            else
                log "ERROR" "Failed to back up $file"
            fi
        fi
    done
}

# Function to delete specified files and folders
delete_files_and_folders() {
    log "INFO" "Deleting specified files and folders..."

    # Delete files that were backed up
    for file in "${files_to_backup_and_remove[@]}"; do
        if [[ -e "$file" ]]; then
            if rm "$file" 2>/dev/null; then
                log "INFO" "Deleted $file"
            else
                log "WARNING" "Failed to delete $file or it does not exist"
            fi
        fi
    done

    # Delete additional files and folders
    for item in "${files_and_folders_to_remove[@]}"; do
        if [[ -e "$item" ]]; then
            if rm -rf "$item" 2>/dev/null; then
                log "INFO" "Deleted $item"
            else
                log "WARNING" "Failed to delete $item or it does not exist"
            fi
        fi
    done
}

# Function to stop a service if it's loaded
stop_service() {
    local service_name="$1"
    if systemctl stop "$service_name" 2>/dev/null; then
        log "INFO" "Stopped $service_name"
    else
        log "WARNING" "Failed to stop $service_name or it is not loaded"
    fi
}

# Function to remove systemd service files
remove_service_files() {
    local service_files=("$@")
    for file in "${service_files[@]}"; do
        if rm "$file" 2>/dev/null; then
            log "INFO" "Removed service file $file"
        else
            log "WARNING" "Failed to remove service file $file or it does not exist"
        fi
    done
}

# Function to install LXC AutoScale
install_lxc_autoscale() {
    log "INFO" "Installing LXC AutoScale..."

    # Disable and stop lxc_autoscale_ml if running. Don't use both at the same time (you can still run api and monitor)
    systemctl disable lxc_autoscale_ml
    systemctl stop lxc_autoscale_ml

    # Stop lxc_autoscale if running
    systemctl stop lxc_autoscale

    # Reload systemd
    systemctl daemon-reload

    # Install needed packages (including new dependencies for refactored modules)
    log "INFO" "Installing required system packages..."
    apt update
    apt install git python3-flask python3-requests python3-paramiko python3-yaml -y
    
    # Verify Python dependencies
    log "INFO" "Verifying Python dependencies..."
    python3 -c "import yaml, requests, paramiko" 2>/dev/null || {
        log "ERROR" "Failed to verify Python dependencies. Installation may fail."
        exit 1
    }
    
    # Create necessary directories
    mkdir -p /etc/lxc_autoscale
    mkdir -p /usr/local/bin/lxc_autoscale

    # Create an empty __init__.py file to treat the directory as a Python package
    touch /usr/local/bin/lxc_autoscale/__init__.py

    # Download and install the configuration file
    curl -sSL -o /etc/lxc_autoscale/lxc_autoscale.yaml https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/lxc_autoscale.yaml

    # Download and install all Python files in the lxc_autoscale directory
    log "INFO" "Downloading core application files..."
    curl -sSL -o /usr/local/bin/lxc_autoscale/lxc_autoscale.py https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/lxc_autoscale.py
    
    log "INFO" "Downloading configuration and utility modules..."
    curl -sSL -o /usr/local/bin/lxc_autoscale/constants.py https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/constants.py
    curl -sSL -o /usr/local/bin/lxc_autoscale/config_manager.py https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/config_manager.py
    curl -sSL -o /usr/local/bin/lxc_autoscale/config.py https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/config.py
    curl -sSL -o /usr/local/bin/lxc_autoscale/error_handler.py https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/error_handler.py
    curl -sSL -o /usr/local/bin/lxc_autoscale/security_validator.py https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/security_validator.py
    
    log "INFO" "Downloading execution and management modules..."
    curl -sSL -o /usr/local/bin/lxc_autoscale/command_executor.py https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/command_executor.py
    curl -sSL -o /usr/local/bin/lxc_autoscale/resource_manager.py https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/resource_manager.py
    curl -sSL -o /usr/local/bin/lxc_autoscale/scaling_orchestrator.py https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/scaling_orchestrator.py
    
    log "INFO" "Downloading scaling modules..."
    curl -sSL -o /usr/local/bin/lxc_autoscale/metrics_calculator.py https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/metrics_calculator.py
    curl -sSL -o /usr/local/bin/lxc_autoscale/resource_scaler.py https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/resource_scaler.py
    curl -sSL -o /usr/local/bin/lxc_autoscale/horizontal_scaler.py https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/horizontal_scaler.py
    curl -sSL -o /usr/local/bin/lxc_autoscale/scaling_manager.py https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/scaling_manager.py
    
    log "INFO" "Downloading utility and support modules..."
    curl -sSL -o /usr/local/bin/lxc_autoscale/lxc_utils.py https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/lxc_utils.py
    curl -sSL -o /usr/local/bin/lxc_autoscale/notification.py https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/notification.py
    curl -sSL -o /usr/local/bin/lxc_autoscale/logging_setup.py https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/logging_setup.py
    curl -sSL -o /usr/local/bin/lxc_autoscale/structured_logger.py https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/structured_logger.py
    curl -sSL -o /usr/local/bin/lxc_autoscale/lock_manager.py https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/lock_manager.py

    # Validate that all required files were downloaded successfully
    log "INFO" "Validating downloaded files..."
    required_files=(
        "/usr/local/bin/lxc_autoscale/lxc_autoscale.py"
        "/usr/local/bin/lxc_autoscale/constants.py"
        "/usr/local/bin/lxc_autoscale/config_manager.py"
        "/usr/local/bin/lxc_autoscale/config.py"
        "/usr/local/bin/lxc_autoscale/error_handler.py"
        "/usr/local/bin/lxc_autoscale/security_validator.py"
        "/usr/local/bin/lxc_autoscale/command_executor.py"
        "/usr/local/bin/lxc_autoscale/resource_manager.py"
        "/usr/local/bin/lxc_autoscale/scaling_orchestrator.py"
        "/usr/local/bin/lxc_autoscale/metrics_calculator.py"
        "/usr/local/bin/lxc_autoscale/resource_scaler.py"
        "/usr/local/bin/lxc_autoscale/horizontal_scaler.py"
        "/usr/local/bin/lxc_autoscale/scaling_manager.py"
        "/usr/local/bin/lxc_autoscale/lxc_utils.py"
        "/usr/local/bin/lxc_autoscale/notification.py"
        "/usr/local/bin/lxc_autoscale/logging_setup.py"
        "/usr/local/bin/lxc_autoscale/structured_logger.py"
        "/usr/local/bin/lxc_autoscale/lock_manager.py"
    )
    
    missing_files=()
    for file in "${required_files[@]}"; do
        if [[ ! -f "$file" ]]; then
            missing_files+=("$file")
        fi
    done
    
    if [[ ${#missing_files[@]} -gt 0 ]]; then
        log "ERROR" "The following required files are missing:"
        for file in "${missing_files[@]}"; do
            log "ERROR" "  - $file"
        done
        log "ERROR" "Installation incomplete. Please check your internet connection and try again."
        exit 1
    else
        log "INFO" "${CHECKMARK} All required files downloaded successfully!"
    fi

    # Download and install the systemd service file
    curl -sSL -o /etc/systemd/system/lxc_autoscale.service https://raw.githubusercontent.com/fabriziosalmi/proxmox-lxc-autoscale/main/lxc_autoscale/lxc_autoscale.service

    # Make the main script executable
    chmod +x /usr/local/bin/lxc_autoscale/lxc_autoscale.py

    # Reload systemd to recognize the new service
    systemctl daemon-reload
    systemctl enable lxc_autoscale.service

    # Validate the Python modules can be imported successfully
    log "INFO" "Validating refactored module imports..."
    cd /usr/local/bin/lxc_autoscale
    python3 -c "
try:
    import constants
    import config_manager
    import error_handler
    import security_validator
    import command_executor
    import metrics_calculator
    import resource_scaler
    import horizontal_scaler
    import scaling_orchestrator
    import structured_logger
    print('✅ All refactored modules imported successfully!')
except ImportError as e:
    print(f'❌ Module import failed: {e}')
    exit(1)
" || {
        log "ERROR" "Module validation failed. The refactored system may not work correctly."
        exit 1
    }

    # Automatically start the service after installation
    if systemctl start lxc_autoscale.service; then
        log "INFO" "${CHECKMARK} Service LXC AutoScale started successfully!"
        
        # Check service status after a brief moment
        sleep 2
        if systemctl is-active --quiet lxc_autoscale.service; then
            log "INFO" "${CHECKMARK} Service is running and stable!"
        else
            log "WARNING" "${CLOCK} Service started but may have issues. Check logs with: journalctl -u lxc_autoscale.service"
        fi
    else
        log "ERROR" "${CROSSMARK} Failed to start Service LXC AutoScale."
        log "ERROR" "Check logs with: journalctl -u lxc_autoscale.service"
        exit 1
    fi
}

# Main script execution
header
backup_files
delete_files_and_folders

# Proceed with LXC AutoScale installation
install_lxc_autoscale

log "INFO" "${CHECKMARK} Installation process complete!"
echo ""
echo "${GREEN}${BOLD}🚀 LXC AutoScale v2.0 Successfully Installed!${RESET}"
echo "=============================================="
echo ""
echo "${BLUE}${BOLD}📋 What's New in v2.0:${RESET}"
echo "• Enhanced security with input validation"
echo "• Modular architecture for better maintenance"
echo "• Structured JSON logging for better observability"
echo "• Automatic retry mechanisms for improved reliability"
echo "• Connection pooling for better performance"
echo "• Comprehensive error handling and recovery"
echo ""
echo "${YELLOW}${BOLD}📍 Next Steps:${RESET}"
echo "1. Edit your configuration: ${BLUE}/etc/lxc_autoscale/lxc_autoscale.yaml${RESET}"
echo "2. Check service status: ${BLUE}systemctl status lxc_autoscale.service${RESET}"
echo "3. View logs: ${BLUE}journalctl -u lxc_autoscale.service -f${RESET}"
echo "4. View structured logs: ${BLUE}tail -f /var/log/lxc_autoscale.log | jq${RESET}"
echo ""
echo "${GREEN}${BOLD}✨ The refactored system provides enterprise-grade reliability and security!${RESET}"
echo ""
