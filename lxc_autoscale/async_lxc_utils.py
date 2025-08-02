"""Asynchronous LXC utilities using Proxmox API.

This module provides async versions of LXC management functions,
optimized for high-performance concurrent operations.
"""

import asyncio
import logging
import json
import os
from typing import Any, Dict, List, Optional, Tuple, Union
from datetime import datetime

try:
    from proxmox_api_client import (
        get_async_proxmox_client, 
        close_async_client,
        ProxmoxAPIError, 
        ProxmoxConnectionError,
        ProxmoxAuthenticationError
    )
    PROXMOX_API_AVAILABLE = True
except ImportError:
    logging.warning("Async Proxmox API client not available. Some features may not work.")
    PROXMOX_API_AVAILABLE = False

from config_manager import config_manager
from lxc_utils import (
    is_ignored, 
    backup_container_settings, 
    load_backup_settings,
    run_command,
    BACKUP_DIR
)


class AsyncLXCUtils:
    """Asynchronous LXC utilities class."""
    
    def __init__(self):
        """Initialize async LXC utilities."""
        self._client = None
        self._semaphore = asyncio.Semaphore(10)  # Limit concurrent API calls
        
    async def get_client(self):
        """Get or create async Proxmox client."""
        if not PROXMOX_API_AVAILABLE:
            raise RuntimeError("Async Proxmox API client not available")
        
        if self._client is None:
            self._client = get_async_proxmox_client()
        return self._client
    
    async def close(self):
        """Close the async client."""
        if self._client:
            await close_async_client()
            self._client = None
    
    async def get_containers(self) -> List[str]:
        """Get list of container IDs asynchronously.
        
        Returns:
            List of container ID strings, excluding ignored ones
        """
        # Try Proxmox API first if available
        if PROXMOX_API_AVAILABLE and config_manager.get_default('use_proxmox_api', True):
            try:
                async with self._semaphore:
                    client = await self.get_client()
                    container_ids = await client.get_container_ids()
                
                # Filter out ignored containers
                filtered_containers = [
                    ctid for ctid in container_ids 
                    if ctid and not is_ignored(ctid)
                ]
                
                logging.debug(f"Found containers via async API: {filtered_containers}")
                return filtered_containers
                
            except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
                logging.warning(f"Async Proxmox API failed, falling back to sync method: {e}")
        
        # Fallback to sync method
        from lxc_utils import get_containers
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, get_containers)
    
    async def is_container_running(self, ctid: str) -> bool:
        """Check if container is running asynchronously.
        
        Args:
            ctid: Container ID
            
        Returns:
            True if container is running
        """
        # Try Proxmox API first if available
        if PROXMOX_API_AVAILABLE and config_manager.get_default('use_proxmox_api', True):
            try:
                async with self._semaphore:
                    client = await self.get_client()
                    running = await client.is_container_running(ctid)
                
                logging.debug(f"Container {ctid} running status via async API: {running}")
                return running
                
            except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
                logging.warning(f"Async API failed for container {ctid}, falling back to sync: {e}")
        
        # Fallback to sync method
        from lxc_utils import is_container_running
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, is_container_running, ctid)
    
    async def get_container_config(self, ctid: str) -> Optional[Dict[str, Any]]:
        """Get container configuration asynchronously.
        
        Args:
            ctid: Container ID
            
        Returns:
            Container configuration dictionary or None
        """
        # Try Proxmox API first if available
        if PROXMOX_API_AVAILABLE and config_manager.get_default('use_proxmox_api', True):
            try:
                async with self._semaphore:
                    client = await self.get_client()
                    container_config = await client.get_container_config(ctid)
                
                # Extract relevant fields for backward compatibility
                settings = {
                    'cores': container_config.get('cores', 1),
                    'memory': container_config.get('memory', 512),
                    'full_config': container_config
                }
                
                logging.debug(f"Retrieved config for container {ctid} via async API")
                return settings
                
            except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
                logging.warning(f"Async API config failed for container {ctid}, falling back to sync: {e}")
        
        # Fallback to sync method
        from lxc_utils import get_container_current_config
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, get_container_current_config, ctid)
    
    async def get_cpu_usage(self, ctid: str) -> float:
        """Get container CPU usage asynchronously.
        
        Args:
            ctid: Container ID
            
        Returns:
            CPU usage percentage (0.0 - 100.0)
        """
        # Try Proxmox API RRD data first if available
        if PROXMOX_API_AVAILABLE and config_manager.get_default('use_proxmox_api', True):
            try:
                async with self._semaphore:
                    client = await self.get_client()
                    rrd_data = await client.get_container_rrd_data(ctid, timeframe='hour')
                
                if rrd_data and len(rrd_data) > 0:
                    # Get the most recent data point
                    latest_data = rrd_data[-1]
                    
                    # Calculate CPU usage percentage
                    cpu_usage = latest_data.get('cpu', 0.0)
                    if isinstance(cpu_usage, (int, float)):
                        # RRD data is typically in decimal format (0.0 - 1.0)
                        cpu_percentage = cpu_usage * 100 if cpu_usage <= 1.0 else cpu_usage
                        cpu_percentage = round(max(min(cpu_percentage, 100.0), 0.0), 2)
                        
                        logging.info("CPU usage for %s via async API RRD: %.2f%%", ctid, cpu_percentage)
                        return cpu_percentage
                        
            except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
                logging.warning(f"Async API RRD failed for container {ctid}, falling back to sync: {e}")
        
        # Fallback to sync method
        from lxc_utils import get_cpu_usage
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, get_cpu_usage, ctid)
    
    async def get_memory_usage(self, ctid: str) -> float:
        """Get container memory usage asynchronously.
        
        Args:
            ctid: Container ID
            
        Returns:
            Memory usage percentage (0.0 - 100.0)
        """
        # Try Proxmox API RRD data first if available
        if PROXMOX_API_AVAILABLE and config_manager.get_default('use_proxmox_api', True):
            try:
                async with self._semaphore:
                    client = await self.get_client()
                    rrd_data = await client.get_container_rrd_data(ctid, timeframe='hour')
                
                if rrd_data and len(rrd_data) > 0:
                    # Get the most recent data point
                    latest_data = rrd_data[-1]
                    
                    # Calculate memory usage percentage
                    mem_used = latest_data.get('mem', 0)
                    mem_max = latest_data.get('maxmem', 1)  # Avoid division by zero
                    
                    if isinstance(mem_used, (int, float)) and isinstance(mem_max, (int, float)) and mem_max > 0:
                        mem_percentage = (mem_used / mem_max) * 100
                        mem_percentage = round(max(min(mem_percentage, 100.0), 0.0), 2)
                        
                        logging.info("Memory usage for %s via async API RRD: %.2f%%", ctid, mem_percentage)
                        return mem_percentage
                        
            except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
                logging.warning(f"Async API RRD failed for container {ctid}, falling back to sync: {e}")
        
        # Fallback to sync method
        from lxc_utils import get_memory_usage
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, get_memory_usage, ctid)
    
    async def scale_container_resources(self, ctid: str, cores: Optional[int] = None, 
                                      memory: Optional[int] = None) -> bool:
        """Scale container resources asynchronously.
        
        Args:
            ctid: Container ID
            cores: New number of CPU cores (optional)
            memory: New memory allocation in MB (optional)
            
        Returns:
            True if scaling was successful
        """
        if not cores and not memory:
            logging.warning(f"No scaling parameters provided for container {ctid}")
            return False
        
        # Try Proxmox API first if available
        if PROXMOX_API_AVAILABLE and config_manager.get_default('use_proxmox_api', True):
            try:
                async with self._semaphore:
                    client = await self.get_client()
                    update_params = {}
                    
                    if cores is not None:
                        update_params['cores'] = cores
                    if memory is not None:
                        update_params['memory'] = memory
                    
                    success = await client.update_container_config(ctid, **update_params)
                
                if success:
                    logging.info(f"Scaled container {ctid} via async API: {update_params}")
                    return True
                    
            except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
                logging.warning(f"Async API scaling failed for container {ctid}, falling back to sync: {e}")
        
        # Fallback to sync method
        from lxc_utils import scale_container_resources
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, scale_container_resources, ctid, cores, memory)
    
    async def clone_container(self, source_ctid: str, new_ctid: str, 
                             hostname: Optional[str] = None) -> bool:
        """Clone container asynchronously.
        
        Args:
            source_ctid: Source container ID
            new_ctid: New container ID
            hostname: Hostname for new container (optional)
            
        Returns:
            True if cloning was successful
        """
        # Try Proxmox API first if available
        if PROXMOX_API_AVAILABLE and config_manager.get_default('use_proxmox_api', True):
            try:
                async with self._semaphore:
                    client = await self.get_client()
                    success = await client.clone_container(source_ctid, new_ctid, hostname=hostname)
                
                if success:
                    logging.info(f"Cloned container {source_ctid} to {new_ctid} via async API")
                    return True
                    
            except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
                logging.warning(f"Async API cloning failed, falling back to sync: {e}")
        
        # Fallback to sync method
        from lxc_utils import clone_container_api
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, clone_container_api, source_ctid, new_ctid, hostname)
    
    async def start_container(self, ctid: str) -> bool:
        """Start container asynchronously.
        
        Args:
            ctid: Container ID
            
        Returns:
            True if start was successful
        """
        # Try Proxmox API first if available
        if PROXMOX_API_AVAILABLE and config_manager.get_default('use_proxmox_api', True):
            try:
                async with self._semaphore:
                    client = await self.get_client()
                    success = await client.start_container(ctid)
                
                if success:
                    logging.info(f"Started container {ctid} via async API")
                    return True
                    
            except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
                logging.warning(f"Async API start failed for container {ctid}, falling back to sync: {e}")
        
        # Fallback to sync method
        from lxc_utils import start_container_api
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, start_container_api, ctid)
    
    async def stop_container(self, ctid: str) -> bool:
        """Stop container asynchronously.
        
        Args:
            ctid: Container ID
            
        Returns:
            True if stop was successful
        """
        # Try Proxmox API first if available
        if PROXMOX_API_AVAILABLE and config_manager.get_default('use_proxmox_api', True):
            try:
                async with self._semaphore:
                    client = await self.get_client()
                    success = await client.stop_container(ctid)
                
                if success:
                    logging.info(f"Stopped container {ctid} via async API")
                    return True
                    
            except (ProxmoxAPIError, ProxmoxConnectionError, ProxmoxAuthenticationError) as e:
                logging.warning(f"Async API stop failed for container {ctid}, falling back to sync: {e}")
        
        # Fallback to sync method
        from lxc_utils import stop_container_api
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, stop_container_api, ctid)
    
    async def get_container_data(self, ctid: str) -> Optional[Dict[str, Any]]:
        """Collect container resource usage data asynchronously.
        
        Args:
            ctid: Container ID
            
        Returns:
            Dictionary containing container resource data or None
        """
        if is_ignored(ctid):
            return None
        
        # Check if container is running
        if not await self.is_container_running(ctid):
            return None
        
        logging.debug("Collecting data for container %s (async)", ctid)
        try:
            # Get current configuration
            config_data = await self.get_container_config(ctid)
            if not config_data:
                logging.error(f"Failed to get configuration for container {ctid}")
                return None
            
            cores = config_data.get('cores', 1)
            memory = config_data.get('memory', 512)
            
            # Backup the configuration (run in executor to avoid blocking)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, backup_container_settings, ctid, config_data)
            
            # Get resource usage concurrently
            cpu_task = asyncio.create_task(self.get_cpu_usage(ctid))
            mem_task = asyncio.create_task(self.get_memory_usage(ctid))
            
            cpu_usage, mem_usage = await asyncio.gather(cpu_task, mem_task)
            
            return {
                "cpu": cpu_usage,
                "mem": mem_usage,
                "initial_cores": cores,
                "initial_memory": memory,
            }
            
        except Exception as e:
            logging.error("Error collecting data for %s (async): %s", ctid, str(e))
            return None
    
    async def collect_data_for_container(self, ctid: str) -> Optional[Dict[str, Dict[str, Any]]]:
        """Collect data for a single container asynchronously.
        
        Args:
            ctid: Container ID
            
        Returns:
            Dictionary with container data or None
        """
        data = await self.get_container_data(ctid)
        if data:
            logging.debug("Data collected for container %s (async): %s", ctid, data)
            return {ctid: data}
        return None
    
    async def collect_container_data(self) -> Dict[str, Dict[str, Any]]:
        """Collect resource usage data for all containers asynchronously.
        
        Returns:
            Dictionary of container resource data
        """
        containers: Dict[str, Dict[str, Any]] = {}
        
        try:
            # Get all container IDs
            container_ids = await self.get_containers()
            
            # Filter out ignored containers
            filtered_ids = [ctid for ctid in container_ids if not is_ignored(ctid)]
            
            if not filtered_ids:
                logging.info("No containers to process")
                return containers
            
            # Create tasks for concurrent data collection
            tasks = [
                asyncio.create_task(self.collect_data_for_container(ctid))
                for ctid in filtered_ids
            ]
            
            # Wait for all tasks to complete
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results
            for i, result in enumerate(results):
                ctid = filtered_ids[i]
                
                if isinstance(result, Exception):
                    logging.error(f"Error collecting data for container {ctid}: {result}")
                    continue
                
                if result:
                    containers.update(result)
                    
                    # Apply tier settings if available
                    from config import LXC_TIER_ASSOCIATIONS
                    if ctid in LXC_TIER_ASSOCIATIONS:
                        tier_config = LXC_TIER_ASSOCIATIONS[ctid]
                        containers[ctid].update(tier_config)
                        logging.info(f"Applied tier settings for container {ctid} from tier {tier_config.get('tier_name', 'unknown')}")
            
            logging.info("Collected data for containers (async): %s", list(containers.keys()))
            return containers
            
        except Exception as e:
            logging.error(f"Error collecting container data (async): {e}")
            return containers


# Global async utilities instance
_async_utils: Optional[AsyncLXCUtils] = None


def get_async_lxc_utils() -> AsyncLXCUtils:
    """Get global async LXC utilities instance."""
    global _async_utils
    if _async_utils is None:
        _async_utils = AsyncLXCUtils()
    return _async_utils


async def close_async_lxc_utils() -> None:
    """Close the global async utilities instance."""
    global _async_utils
    if _async_utils:
        await _async_utils.close()
        _async_utils = None