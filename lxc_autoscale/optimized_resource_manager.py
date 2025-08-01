"""Optimized resource management with advanced allocation algorithms."""

import asyncio
import logging
import time
from typing import Any, Dict, List, Tuple, Optional, Set
from dataclasses import dataclass
from enum import Enum
import heapq
from concurrent.futures import ThreadPoolExecutor

from performance_cache import cached, get_global_cache
from async_command_executor import AsyncCommandExecutor


class ResourceType(Enum):
    """Types of resources that can be managed."""
    CPU = "cpu"
    MEMORY = "memory"


class ScalingAction(Enum):
    """Types of scaling actions."""
    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down" 
    NO_ACTION = "no_action"


@dataclass
class ResourceRequest:
    """Represents a resource scaling request."""
    container_id: str
    resource_type: ResourceType
    action: ScalingAction
    current_value: int
    requested_value: int
    priority: float
    urgency: float
    tier_config: Dict[str, Any]
    
    def __lt__(self, other):
        """For priority queue ordering - higher priority/urgency first."""
        return (self.priority * self.urgency) > (other.priority * other.urgency)


class OptimizedResourceManager:
    """High-performance resource manager with advanced allocation algorithms."""
    
    def __init__(self, config_manager, async_executor: AsyncCommandExecutor, metrics_calculator):
        """Initialize the optimized resource manager.
        
        Args:
            config_manager: Configuration manager instance
            async_executor: Async command executor instance
            metrics_calculator: Metrics calculator instance
        """
        self.config_manager = config_manager
        self.async_executor = async_executor
        self.metrics_calculator = metrics_calculator
        self.cache = get_global_cache()
        
        # Resource allocation tracking
        self._resource_locks = {
            ResourceType.CPU: asyncio.Lock(),
            ResourceType.MEMORY: asyncio.Lock()
        }
        
        # Performance metrics
        self._allocation_stats = {
            'total_requests': 0,
            'successful_allocations': 0,
            'failed_allocations': 0,
            'avg_allocation_time': 0.0,
            'resource_utilization': {'cpu': 0.0, 'memory': 0.0}
        }
        
        # Thread pool for CPU-intensive calculations
        self._thread_pool = ThreadPoolExecutor(max_workers=4)
    
    @cached(ttl=60.0, key_prefix="resource_availability_")
    async def get_available_resources(self) -> Tuple[int, int]:
        """Get available CPU cores and memory with caching.
        
        Returns:
            Tuple of (available_cores, available_memory_mb)
        """
        # Import here to avoid circular imports
        from lxc_utils import get_total_cores, get_total_memory
        
        # Run in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        total_cores = await loop.run_in_executor(self._thread_pool, get_total_cores)
        total_memory = await loop.run_in_executor(self._thread_pool, get_total_memory)
        
        reserve_cpu_percent = self.config_manager.get_default('reserve_cpu_percent', 10)
        reserve_memory_mb = self.config_manager.get_default('reserve_memory_mb', 2048)
        
        reserved_cores = max(1, int(total_cores * reserve_cpu_percent / 100))
        available_cores = total_cores - reserved_cores
        available_memory = total_memory - reserve_memory_mb
        
        return available_cores, available_memory
    
    async def process_containers_optimized(
        self, 
        containers_data: Dict[str, Dict[str, Any]], 
        energy_mode: bool = False
    ) -> Dict[str, Any]:
        """Process multiple containers with optimized resource allocation.
        
        Args:
            containers_data: Dictionary containing container resource usage data
            energy_mode: Flag to enable energy efficiency mode
            
        Returns:
            Dictionary with processing results and statistics
        """
        start_time = time.time()
        logging.info(f"Starting optimized resource processing for {len(containers_data)} containers")
        
        # Get current resource availability
        available_cores, available_memory = await self.get_available_resources()
        
        # Build resource requests with priority scoring
        resource_requests = await self._build_resource_requests(
            containers_data, available_cores, available_memory, energy_mode
        )
        
        # Process requests in optimized order
        results = await self._process_requests_batch(resource_requests)
        
        # Update statistics
        processing_time = time.time() - start_time
        self._update_allocation_stats(len(containers_data), results, processing_time)
        
        logging.info(f"Optimized resource processing completed in {processing_time:.2f}s")
        
        return {
            'processing_time': processing_time,
            'total_containers': len(containers_data),
            'successful_operations': sum(1 for r in results if r['success']),
            'failed_operations': sum(1 for r in results if not r['success']),
            'results': results
        }
    
    async def _build_resource_requests(
        self,
        containers_data: Dict[str, Dict[str, Any]],
        available_cores: int,
        available_memory: int,
        energy_mode: bool
    ) -> List[ResourceRequest]:
        """Build prioritized resource requests from container data.
        
        Args:
            containers_data: Container data dictionary
            available_cores: Available CPU cores
            available_memory: Available memory in MB
            energy_mode: Energy efficiency mode flag
            
        Returns:
            List of prioritized resource requests
        """
        requests = []
        
        for ctid, usage_data in containers_data.items():
            if self.config_manager.is_ignored(ctid):
                continue
            
            tier_config = self.config_manager.get_tier_config(ctid)
            
            # Calculate CPU requests
            cpu_request = await self._calculate_cpu_request(
                ctid, usage_data, tier_config, energy_mode
            )
            if cpu_request:
                requests.append(cpu_request)
            
            # Calculate memory requests
            memory_request = await self._calculate_memory_request(
                ctid, usage_data, tier_config, energy_mode
            )
            if memory_request:
                requests.append(memory_request)
        
        # Sort by priority (highest first)
        requests.sort(reverse=True)
        
        return requests
    
    async def _calculate_cpu_request(
        self,
        ctid: str,
        usage_data: Dict[str, Any],
        tier_config: Dict[str, Any],
        energy_mode: bool
    ) -> Optional[ResourceRequest]:
        """Calculate CPU scaling request for a container.
        
        Args:
            ctid: Container ID
            usage_data: Container usage data
            tier_config: Tier configuration
            energy_mode: Energy efficiency mode flag
            
        Returns:
            ResourceRequest or None if no action needed
        """
        current_cores = usage_data["initial_cores"]
        cpu_usage = usage_data['cpu']
        cpu_upper = tier_config['cpu_upper_threshold']
        cpu_lower = tier_config['cpu_lower_threshold']
        min_cores = tier_config['min_cores']
        max_cores = tier_config['max_cores']
        
        # Determine scaling action
        action = ScalingAction.NO_ACTION
        requested_cores = current_cores
        priority = 0.0
        urgency = 0.0
        
        if energy_mode and self.metrics_calculator.is_off_peak():
            # Energy mode: scale down to minimum
            if current_cores > min_cores:
                action = ScalingAction.SCALE_DOWN
                requested_cores = min_cores
                priority = 3.0  # Lower priority for energy savings
                urgency = 0.5
        elif cpu_usage > cpu_upper:
            # Scale up CPU
            increment = self.metrics_calculator.calculate_increment(
                cpu_usage, cpu_upper,
                tier_config.get('core_min_increment', 1),
                tier_config.get('core_max_increment', 2)
            )
            requested_cores = min(max_cores, current_cores + increment)
            
            if requested_cores > current_cores:
                action = ScalingAction.SCALE_UP
                priority = min(10.0, cpu_usage / 10.0)  # Higher priority for higher usage
                urgency = max(1.0, (cpu_usage - cpu_upper) / 10.0)
        elif cpu_usage < cpu_lower and current_cores > min_cores:
            # Scale down CPU
            decrement = self.metrics_calculator.calculate_decrement(
                cpu_usage, cpu_lower, current_cores,
                tier_config.get('core_min_increment', 1), min_cores
            )
            requested_cores = max(min_cores, current_cores - decrement)
            
            if requested_cores < current_cores:
                action = ScalingAction.SCALE_DOWN
                priority = 5.0 - (cpu_usage / 20.0)  # Lower priority for scale down
                urgency = max(0.5, (cpu_lower - cpu_usage) / 20.0)
        
        if action != ScalingAction.NO_ACTION:
            return ResourceRequest(
                container_id=ctid,
                resource_type=ResourceType.CPU,
                action=action,
                current_value=current_cores,
                requested_value=requested_cores,
                priority=priority,
                urgency=urgency,
                tier_config=tier_config
            )
        
        return None
    
    async def _calculate_memory_request(
        self,
        ctid: str,
        usage_data: Dict[str, Any],
        tier_config: Dict[str, Any],
        energy_mode: bool
    ) -> Optional[ResourceRequest]:
        """Calculate memory scaling request for a container.
        
        Args:
            ctid: Container ID
            usage_data: Container usage data
            tier_config: Tier configuration
            energy_mode: Energy efficiency mode flag
            
        Returns:
            ResourceRequest or None if no action needed
        """
        current_memory = usage_data["initial_memory"]
        mem_usage = usage_data['mem']
        mem_upper = tier_config['memory_upper_threshold']
        mem_lower = tier_config['memory_lower_threshold']
        min_memory = tier_config['min_memory']
        
        # Convert memory usage to percentage
        mem_usage_percent = (mem_usage / current_memory) * 100
        
        # Determine scaling action
        action = ScalingAction.NO_ACTION
        requested_memory = current_memory
        priority = 0.0
        urgency = 0.0
        
        behavior_multiplier = self.metrics_calculator.get_behavior_multiplier()
        
        if energy_mode and self.metrics_calculator.is_off_peak():
            # Energy mode: scale down to minimum
            if current_memory > min_memory:
                action = ScalingAction.SCALE_DOWN
                requested_memory = min_memory
                priority = 3.0  # Lower priority for energy savings
                urgency = 0.5
        elif mem_usage_percent > mem_upper:
            # Scale up memory
            increment = max(
                int(tier_config.get('memory_min_increment', 256) * behavior_multiplier),
                int((mem_usage_percent - mem_upper) * tier_config.get('memory_min_increment', 256) / 10.0)
            )
            requested_memory = current_memory + increment
            
            action = ScalingAction.SCALE_UP
            priority = min(10.0, mem_usage_percent / 10.0)
            urgency = max(1.0, (mem_usage_percent - mem_upper) / 10.0)
        elif mem_usage_percent < mem_lower and current_memory > min_memory:
            # Scale down memory
            decrement = self.metrics_calculator.calculate_decrement(
                mem_usage_percent, mem_lower, current_memory,
                int(tier_config.get('min_decrease_chunk', 128) * behavior_multiplier), 
                min_memory
            )
            requested_memory = max(min_memory, current_memory - decrement)
            
            if requested_memory < current_memory:
                action = ScalingAction.SCALE_DOWN
                priority = 4.0 - (mem_usage_percent / 25.0)
                urgency = max(0.5, (mem_lower - mem_usage_percent) / 20.0)
        
        if action != ScalingAction.NO_ACTION:
            return ResourceRequest(
                container_id=ctid,
                resource_type=ResourceType.MEMORY,
                action=action,
                current_value=current_memory,
                requested_value=requested_memory,
                priority=priority,
                urgency=urgency,
                tier_config=tier_config
            )
        
        return None
    
    async def _process_requests_batch(self, requests: List[ResourceRequest]) -> List[Dict[str, Any]]:
        """Process resource requests in optimized batches.
        
        Args:
            requests: List of resource requests
            
        Returns:
            List of processing results
        """
        if not requests:
            return []
        
        # Group requests by resource type for concurrent processing
        cpu_requests = [r for r in requests if r.resource_type == ResourceType.CPU]
        memory_requests = [r for r in requests if r.resource_type == ResourceType.MEMORY]
        
        # Process both resource types concurrently
        tasks = []
        if cpu_requests:
            tasks.append(self._process_cpu_requests(cpu_requests))
        if memory_requests:
            tasks.append(self._process_memory_requests(memory_requests))
        
        # Wait for all processing to complete
        results = []
        if tasks:
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for batch_result in batch_results:
                if isinstance(batch_result, list):
                    results.extend(batch_result)
                else:
                    logging.error(f"Error processing resource batch: {batch_result}")
        
        return results
    
    async def _process_cpu_requests(self, requests: List[ResourceRequest]) -> List[Dict[str, Any]]:
        """Process CPU scaling requests.
        
        Args:
            requests: List of CPU resource requests
            
        Returns:
            List of processing results
        """
        results = []
        available_cores, _ = await self.get_available_resources()
        
        async with self._resource_locks[ResourceType.CPU]:
            # Build batch commands for concurrent execution
            commands = []
            request_map = {}
            
            for i, request in enumerate(requests):
                if request.action == ScalingAction.SCALE_UP:
                    needed_cores = request.requested_value - request.current_value
                    if available_cores >= needed_cores:
                        cmd = f"pct set {request.container_id} -cores {request.requested_value}"
                        commands.append((cmd, 30))  # 30 second timeout
                        request_map[i] = request
                        available_cores -= needed_cores
                elif request.action == ScalingAction.SCALE_DOWN:
                    cmd = f"pct set {request.container_id} -cores {request.requested_value}"
                    commands.append((cmd, 30))
                    request_map[i] = request
                    available_cores += (request.current_value - request.requested_value)
            
            # Execute commands in batch
            if commands:
                batch_results = await self.async_executor.execute_proxmox_commands_batch(commands)
                
                for i, result in enumerate(batch_results):
                    if i in request_map:
                        request = request_map[i]
                        success = result is not None
                        
                        results.append({
                            'container_id': request.container_id,
                            'resource_type': 'cpu',
                            'action': request.action.value,
                            'success': success,
                            'old_value': request.current_value,
                            'new_value': request.requested_value if success else request.current_value
                        })
                        
                        if success:
                            logging.info(f"CPU scaling successful for container {request.container_id}: "
                                       f"{request.current_value} -> {request.requested_value} cores")
                        else:
                            logging.error(f"CPU scaling failed for container {request.container_id}")
        
        return results
    
    async def _process_memory_requests(self, requests: List[ResourceRequest]) -> List[Dict[str, Any]]:
        """Process memory scaling requests.
        
        Args:
            requests: List of memory resource requests
            
        Returns:
            List of processing results
        """
        results = []
        _, available_memory = await self.get_available_resources()
        
        async with self._resource_locks[ResourceType.MEMORY]:
            # Build batch commands for concurrent execution
            commands = []
            request_map = {}
            
            for i, request in enumerate(requests):
                if request.action == ScalingAction.SCALE_UP:
                    needed_memory = request.requested_value - request.current_value
                    if available_memory >= needed_memory:
                        cmd = f"pct set {request.container_id} -memory {request.requested_value}"
                        commands.append((cmd, 30))
                        request_map[i] = request
                        available_memory -= needed_memory
                elif request.action == ScalingAction.SCALE_DOWN:
                    cmd = f"pct set {request.container_id} -memory {request.requested_value}"
                    commands.append((cmd, 30))
                    request_map[i] = request
                    available_memory += (request.current_value - request.requested_value)
            
            # Execute commands in batch
            if commands:
                batch_results = await self.async_executor.execute_proxmox_commands_batch(commands)
                
                for i, result in enumerate(batch_results):
                    if i in request_map:
                        request = request_map[i]
                        success = result is not None
                        
                        results.append({
                            'container_id': request.container_id,
                            'resource_type': 'memory',
                            'action': request.action.value,
                            'success': success,
                            'old_value': request.current_value,
                            'new_value': request.requested_value if success else request.current_value
                        })
                        
                        if success:
                            logging.info(f"Memory scaling successful for container {request.container_id}: "
                                       f"{request.current_value} -> {request.requested_value} MB")
                        else:
                            logging.error(f"Memory scaling failed for container {request.container_id}")
        
        return results
    
    def _update_allocation_stats(self, total_containers: int, results: List[Dict], processing_time: float) -> None:
        """Update allocation statistics.
        
        Args:
            total_containers: Total number of containers processed
            results: Processing results
            processing_time: Time taken for processing
        """
        successful = sum(1 for r in results if r['success'])
        failed = len(results) - successful
        
        self._allocation_stats['total_requests'] += total_containers
        self._allocation_stats['successful_allocations'] += successful
        self._allocation_stats['failed_allocations'] += failed
        
        # Update average processing time
        total_requests = self._allocation_stats['total_requests']
        current_avg = self._allocation_stats['avg_allocation_time']
        self._allocation_stats['avg_allocation_time'] = (
            (current_avg * (total_requests - total_containers) + processing_time) / total_requests
        )
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """Get resource manager performance statistics."""
        stats = self._allocation_stats.copy()
        
        if stats['total_requests'] > 0:
            stats['success_rate'] = (stats['successful_allocations'] / stats['total_requests']) * 100
        else:
            stats['success_rate'] = 0.0
        
        # Add cache statistics
        stats['cache_stats'] = self.cache.get_stats()
        stats['cache_memory'] = self.cache.get_memory_usage()
        
        return stats
    
    async def cleanup(self) -> None:
        """Cleanup resources used by the manager."""
        try:
            self._thread_pool.shutdown(wait=True)
            logging.info("Optimized resource manager cleanup completed")
        except Exception as e:
            logging.error(f"Error during resource manager cleanup: {e}")