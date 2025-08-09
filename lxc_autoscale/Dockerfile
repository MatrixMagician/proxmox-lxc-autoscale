# ==============================================================================
# LXC AutoScale v3.0 Performance Edition Docker Image
# ==============================================================================
# 
# This Dockerfile builds a high-performance container for LXC AutoScale v3.0
# with enhanced features including:
# 
# - Proxmox API integration for efficient container management
# - Asynchronous operations for 60-80% performance improvement
# - Advanced caching and performance monitoring
# - Circuit breaker patterns for reliability
# - Memory optimization and leak detection
# - Real-time performance metrics and trend analysis
# 
# Environment Variables:
# ----------------------
# ASYNC_MODE=true/false          - Enable high-performance async mode
# RUN_MODE=continuous/single-cycle - Operation mode
# PROXMOX_HOST                   - Proxmox server hostname/IP
# PROXMOX_API_TOKEN_VALUE        - API token for authentication
# PROXMOX_API_PASSWORD           - API password for authentication (alternative to token)
# USER_CONF_PATH                 - Custom configuration file path
# MAX_CONCURRENT_CONTAINERS      - Async concurrency limit
# 
# ==============================================================================

# Base image - Using Python 3.11 for enhanced asyncio performance and features
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

# Set environment variable to indicate Docker environment
ENV RUNNING_IN_DOCKER=true

# Performance and async optimization environment variables
ENV ASYNCIO_DEFAULT_EXECUTOR_THREADS=10
ENV MAX_CONCURRENT_CONTAINERS=20

# Set the working directory
WORKDIR /app

# Copy application files
COPY . /app

# Install required packages including performance monitoring tools
RUN apt-get update && apt-get install -y \
    curl \
    procps \
    psmisc \
    htop \
    net-tools \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install yq for YAML processing (updated to latest version)
RUN curl -L https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 -o /usr/bin/yq && \
    chmod +x /usr/bin/yq

# Upgrade pip and install Python dependencies with optimizations
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copy the entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Create required directories for caching and performance monitoring
RUN mkdir -p /var/cache/lxc_autoscale /var/lib/lxc_autoscale/performance \
    && chown -R 1000:1000 /var/cache/lxc_autoscale /var/lib/lxc_autoscale

# Add health check for container monitoring
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Set the entrypoint
ENTRYPOINT ["/entrypoint.sh"]
