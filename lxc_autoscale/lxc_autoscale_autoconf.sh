#!/bin/bash

set -e  # Exit immediately if a command exits with a non-zero status.

# Function to get total CPU cores using /proc/cpuinfo
get_total_cores() {
  grep -c '^processor' /proc/cpuinfo
}

# Function to get total memory in MB using free command
get_total_memory() {
  free -m | awk '/^Mem:/{print $2}'
}

# Function to get list of running LXC containers
get_running_lxc_containers() {
  pct list | grep running | awk '{print $1}'
}

# Function to get list of stopped LXC containers
get_stopped_lxc_containers() {
  pct list | grep stopped | awk '{print $1}'
}

# Function to get the number of cores for a specific LXC container
get_container_cores() {
  pct config $1 | grep cores | awk '{print $2}'
}

# Function to get the memory allocated to a specific LXC container in MB
get_container_memory() {
  pct config $1 | grep memory | awk '{print $2}'
}

# Function to ask for user confirmation or provide a default value
ask_for_confirmation() {
  local prompt="$1"
  local default_value="$2"
  read -p "💬 $prompt [$default_value]: " input
  echo "${input:-$default_value}"
}

# Function to validate numeric ranges
validate_percentage() {
  local value="$1"
  local name="$2"
  if [[ "$value" =~ ^[0-9]+$ ]] && [ "$value" -ge 0 ] && [ "$value" -le 100 ]; then
    return 0
  else
    echo "⚠️  Warning: $name should be between 0-100. Value '$value' may cause issues." >&2
    return 1
  fi
}

# Function to validate positive integers
validate_positive_integer() {
  local value="$1"
  local name="$2"
  if [[ "$value" =~ ^[0-9]+$ ]] && [ "$value" -gt 0 ]; then
    return 0
  else
    echo "⚠️  Warning: $name should be a positive integer. Value '$value' may cause issues." >&2
    return 1
  fi
}

# Modified Function to prompt for containers to ignore
ask_for_ignored_containers() {
  local containers=("$@")
  local ignored=()

  echo "💻 Detected running LXC containers: ${containers[*]}" >&2
  read -p "Enter the IDs of the containers you want to ignore, separated by spaces or commas (e.g., 100,101,102) or press Enter to skip: " ignored_ids

  if [ -n "$ignored_ids" ]; then
    IFS=', ' read -ra ignored_array <<< "$ignored_ids"
    for id in "${ignored_array[@]}"; do
      if [[ "$id" =~ ^[0-9]+$ ]] && [[ " ${containers[*]} " =~ " $id " ]]; then
        ignored+=("$id")
      else
        echo "⚠️  Warning: Container ID $id is either not numeric or not in the list of running containers." >&2
      fi
    done
  fi

  if [ ${#ignored[@]} -eq 0 ]; then
    echo "⚠️ No containers were ignored." >&2
  else
    echo "🚫 Ignored containers: ${ignored[*]}" >&2
  fi

  printf '%s\n' "${ignored[@]}"
}




# Start of the script
echo "🚀 Starting LXC AutoScale v2.0 Configuration Generator..."
echo "✨ Enhanced with new enterprise features!"
echo ""

# Gather system information
total_cores=$(get_total_cores)
total_memory=$(get_total_memory)

# Get lists of running and stopped LXC containers
running_containers=($(get_running_lxc_containers))
stopped_containers=($(get_stopped_lxc_containers))

# Print out the lists of containers
echo "📊 Total containers: $((${#running_containers[@]} + ${#stopped_containers[@]})) (Running: ${#running_containers[@]}, Stopped: ${#stopped_containers[@]})"
echo "🛑 Stopped containers: ${stopped_containers[*]}"
echo "✅ Running containers: ${running_containers[*]}"


# Ask the user which containers to ignore
echo "Preparing to ask about ignored containers..."
mapfile -t ignored_containers < <(ask_for_ignored_containers "${running_containers[@]}")

# Debug output
echo "Debug: Ignored containers: ${ignored_containers[*]}"
echo "Debug: Running containers before filtering: ${running_containers[*]}"

# Filter out ignored containers from the list of LXC containers to process
processed_containers=()
for ctid in "${running_containers[@]}"; do
  if [[ ! " ${ignored_containers[*]} " =~ " $ctid " ]]; then
    processed_containers+=("$ctid")
  fi
done

# Debug output
echo "Debug: Processed containers after filtering: ${processed_containers[*]}"

# If no containers are left after ignoring, exit
if [ ${#processed_containers[@]} -eq 0 ]; then
  echo "❌ No containers left to process after applying ignore list. Exiting..."
  exit 1
fi

# Prepare to calculate the total used resources
total_used_cores=0
total_used_memory=0

# Gather resources for each container
for ctid in "${processed_containers[@]}"; do
  cores=$(get_container_cores $ctid)
  memory=$(get_container_memory $ctid)
  total_used_cores=$((total_used_cores + cores))
  total_used_memory=$((total_used_memory + memory))
done

# Display the total resources used and available
echo "🔍 Total resources on Proxmox host: $total_cores cores, $total_memory MB memory"
echo "🔍 Total resources used by selected containers: $total_used_cores cores, $total_used_memory MB memory"
echo "🔍 Remaining resources: $((total_cores - total_used_cores)) cores, $((total_memory - total_used_memory)) MB memory"

# Ask for confirmation for DEFAULT section settings (updated with v2.0 defaults)
echo "⚙️  Configuring DEFAULT settings with v2.0 enhanced defaults..."
echo ""
poll_interval=$(ask_for_confirmation "Polling interval (seconds)" "300")
cpu_upper_threshold=$(ask_for_confirmation "CPU upper threshold (%)" "80")
cpu_lower_threshold=$(ask_for_confirmation "CPU lower threshold (%)" "20")
memory_upper_threshold=$(ask_for_confirmation "Memory upper threshold (%)" "80")
memory_lower_threshold=$(ask_for_confirmation "Memory lower threshold (%)" "20")
core_min_increment=$(ask_for_confirmation "Minimum core increment" "1")
core_max_increment=$(ask_for_confirmation "Maximum core increment" "2")
memory_min_increment=$(ask_for_confirmation "Minimum memory increment (MB)" "256")
min_decrease_chunk=$(ask_for_confirmation "Minimum memory decrease chunk (MB)" "128")
reserve_cpu_percent=$(ask_for_confirmation "Reserved CPU percentage" "10")
reserve_memory_mb=$(ask_for_confirmation "Reserved memory (MB)" "2048")
log_file=$(ask_for_confirmation "Log file path" "/var/log/lxc_autoscale.log")
lock_file=$(ask_for_confirmation "Lock file path" "/var/lock/lxc_autoscale.lock")
backup_dir=$(ask_for_confirmation "Backup directory" "/var/lib/lxc_autoscale/backups")
off_peak_start=$(ask_for_confirmation "Off-peak start hour" "22")
off_peak_end=$(ask_for_confirmation "Off-peak end hour" "6")
energy_mode=$(ask_for_confirmation "Enable energy-saving mode (true/false)" "false")
behaviour=$(ask_for_confirmation "Behaviour (normal/conservative/aggressive)" "normal")

echo ""
echo "🔧 Advanced v2.0 Configuration Options:"
cpu_scale_divisor=$(ask_for_confirmation "CPU scale divisor (for scaling calculations)" "2.0")
memory_scale_factor=$(ask_for_confirmation "Memory scale factor" "1.5")
timeout_extended=$(ask_for_confirmation "Extended timeout for operations (seconds)" "60")

echo ""
echo "🌐 SSH/Remote Configuration (optional - leave empty for local execution):"
ssh_host=$(ask_for_confirmation "SSH host (leave empty for local execution)" "")
ssh_user=$(ask_for_confirmation "SSH username (if using remote execution)" "")
ssh_port=$(ask_for_confirmation "SSH port (if using remote execution)" "22")
use_remote_proxmox=$(ask_for_confirmation "Use remote Proxmox execution (true/false)" "false")

echo ""
echo "🔍 Validating configuration values..."
# Validate percentage values
validate_percentage "$cpu_upper_threshold" "CPU upper threshold"
validate_percentage "$cpu_lower_threshold" "CPU lower threshold"
validate_percentage "$memory_upper_threshold" "Memory upper threshold"
validate_percentage "$memory_lower_threshold" "Memory lower threshold"
validate_percentage "$reserve_cpu_percent" "Reserved CPU percentage"

# Validate positive integers
validate_positive_integer "$poll_interval" "Poll interval"
validate_positive_integer "$core_min_increment" "Core min increment"
validate_positive_integer "$core_max_increment" "Core max increment"
validate_positive_integer "$memory_min_increment" "Memory min increment"
validate_positive_integer "$min_decrease_chunk" "Min decrease chunk"
validate_positive_integer "$reserve_memory_mb" "Reserved memory"

# Validate threshold relationships
if [ "$cpu_lower_threshold" -ge "$cpu_upper_threshold" ]; then
  echo "⚠️  Warning: CPU lower threshold ($cpu_lower_threshold%) should be less than upper threshold ($cpu_upper_threshold%)" >&2
fi

if [ "$memory_lower_threshold" -ge "$memory_upper_threshold" ]; then
  echo "⚠️  Warning: Memory lower threshold ($memory_lower_threshold%) should be less than upper threshold ($memory_upper_threshold%)" >&2
fi

echo "✅ Basic validation completed!"
echo ""

# Prepare YAML content with v2.0 enhancements
yaml_content="# LXC AutoScale v2.0 Configuration
# Generated by lxc_autoscale_autoconf.sh on $(date +"%Y-%m-%d %H:%M:%S")
# Enhanced with enterprise features: security, modularity, structured logging

DEFAULT:
  # Core scaling parameters
  poll_interval: $poll_interval
  cpu_upper_threshold: $cpu_upper_threshold
  cpu_lower_threshold: $cpu_lower_threshold
  memory_upper_threshold: $memory_upper_threshold
  memory_lower_threshold: $memory_lower_threshold
  
  # Scaling increments and limits
  core_min_increment: $core_min_increment
  core_max_increment: $core_max_increment
  memory_min_increment: $memory_min_increment
  min_decrease_chunk: $min_decrease_chunk
  
  # Resource reservation
  reserve_cpu_percent: $reserve_cpu_percent
  reserve_memory_mb: $reserve_memory_mb
  
  # File paths
  log_file: $log_file
  lock_file: $lock_file
  backup_dir: $backup_dir
  
  # Energy efficiency
  off_peak_start: $off_peak_start
  off_peak_end: $off_peak_end
  energy_mode: $energy_mode
  behaviour: $behaviour
  
  # Advanced v2.0 settings
  cpu_scale_divisor: $cpu_scale_divisor
  memory_scale_factor: $memory_scale_factor
  timeout_extended: $timeout_extended
"

# Add SSH configuration if provided
if [ -n "$ssh_host" ]; then
  yaml_content+="  
  # SSH/Remote configuration
  use_remote_proxmox: $use_remote_proxmox
  proxmox_host: $ssh_host"
  
  if [ -n "$ssh_user" ]; then
    yaml_content+="
  ssh_user: $ssh_user"
  fi
  
  if [ "$ssh_port" != "22" ]; then
    yaml_content+="
  ssh_port: $ssh_port"
  fi
fi

# Add ignore list
yaml_content+="
  
  # Containers to ignore from scaling
  ignore_lxc:"

if [ ${#ignored_containers[@]} -eq 0 ]; then
  yaml_content+=" []"
else
  yaml_content+="\n$(printf '    - %s\n' "${ignored_containers[@]}")"
fi

yaml_content+="\n"

# Generate TIER_ sections for each processed LXC container
echo "⚙️  Configuring TIER sections..."
for ctid in "${processed_containers[@]}"; do
  cores=$(get_container_cores $ctid)
  memory=$(get_container_memory $ctid)

  tier_cpu_upper_threshold=$(ask_for_confirmation "CPU upper threshold for TIER_$ctid (%)" "80")
  tier_cpu_lower_threshold=$(ask_for_confirmation "CPU lower threshold for TIER_$ctid (%)" "20")
  tier_memory_upper_threshold=$(ask_for_confirmation "Memory upper threshold for TIER_$ctid (%)" "80")
  tier_memory_lower_threshold=$(ask_for_confirmation "Memory lower threshold for TIER_$ctid (%)" "20")
  tier_min_cores=$cores
  tier_max_cores=$(ask_for_confirmation "Maximum cores for TIER_$ctid" "$((cores + 2))")
  tier_min_memory=$memory
  tier_core_min_increment=$(ask_for_confirmation "Core increment for TIER_$ctid" "1")
  tier_core_max_increment=$(ask_for_confirmation "Max core increment for TIER_$ctid" "2")
  tier_memory_min_increment=$(ask_for_confirmation "Memory increment (MB) for TIER_$ctid" "256")
  tier_min_decrease_chunk=$(ask_for_confirmation "Min decrease chunk (MB) for TIER_$ctid" "128")

  yaml_content+="
# Container-specific tier configuration
TIER_$ctid:
  # Thresholds
  cpu_upper_threshold: $tier_cpu_upper_threshold
  cpu_lower_threshold: $tier_cpu_lower_threshold
  memory_upper_threshold: $tier_memory_upper_threshold
  memory_lower_threshold: $tier_memory_lower_threshold
  
  # Resource limits
  min_cores: $tier_min_cores
  max_cores: $tier_max_cores
  min_memory: $tier_min_memory
  
  # Scaling increments (v2.0 enhancement)
  core_min_increment: $tier_core_min_increment
  core_max_increment: $tier_core_max_increment
  memory_min_increment: $tier_memory_min_increment
  min_decrease_chunk: $tier_min_decrease_chunk
  
  # Containers in this tier
  lxc_containers:
    - $ctid
"
done

# Add validation and footer information
yaml_content+="
# Configuration validation notes:
# - All thresholds should be between 0-100%
# - Lower thresholds must be less than upper thresholds
# - Minimum resources must be less than or equal to maximum resources
# - SSH configuration is only needed for remote Proxmox execution
# 
# v2.0 Features enabled:
# ✅ Enhanced security with input validation
# ✅ Modular architecture for better maintenance  
# ✅ Structured JSON logging for observability
# ✅ Automatic retry mechanisms for reliability
# ✅ Connection pooling for better performance
# ✅ Centralized error handling and recovery

# Generated by lxc_autoscale_autoconf.sh v2.0 on $(date +"%Y-%m-%d %H:%M:%S")
""

# Final confirmation before saving
echo ""
echo "📝 Configuration has been generated:"
echo "$yaml_content"

# Ask the user where to save the configuration
read -p "💾 Do you want to save this configuration to /etc/lxc_autoscale/lxc_autoscale.yaml? (yes/no): " confirm_save

if [ "$confirm_save" == "yes" ]; then
  # Save to /etc/lxc_autoscale/lxc_autoscale.yaml and restart the service
  echo "$yaml_content" | tee /etc/lxc_autoscale/lxc_autoscale.yaml > /dev/null
  echo "✅ Configuration saved to /etc/lxc_autoscale/lxc_autoscale.yaml."
  echo "🔄 Restarting lxc_autoscale.service..."
  sudo systemctl restart lxc_autoscale.service
  echo "✅ lxc_autoscale.service restarted."
else
  # Save to a timestamped file in the current directory
  timestamp=$(date +"%Y%m%d%H%M%S")
  filename="lxc_autoscale_generated_conf_$timestamp.yaml"
  echo "$yaml_content" > "$filename"
  echo "💾 Configuration saved t $filename"
fi

echo ""
echo "🚀 LXC AutoScale v2.0 Configuration Generated Successfully!"
echo "======================================================="
echo ""
echo "✨ Your configuration includes v2.0 enterprise features:"
echo "• Enhanced security with input validation"
echo "• Modular architecture for better maintenance"
echo "• Structured JSON logging for observability"
echo "• Automatic retry mechanisms for reliability"
echo "• Configuration validation and type safety"
echo ""
echo "📋 Next Steps:"
echo "1. Review the generated configuration above"
echo "2. Check service status: systemctl status lxc_autoscale.service"
echo "3. Monitor logs: journalctl -u lxc_autoscale.service -f"
echo "4. View structured logs: tail -f /var/log/lxc_autoscale.log | jq"
echo ""
echo "🏁 LXC AutoScale v2.0 configuration process completed!"
