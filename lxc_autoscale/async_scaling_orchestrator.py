"""High-performance async orchestrator for LXC container scaling operations."""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from contextlib import asynccontextmanager

from async_command_executor import AsyncCommandExecutor
from config_manager import config_manager
from error_handler import ErrorHandler, safe_execute
from horizontal_scaler import HorizontalScaler
from lxc_utils import log_json_event
from metrics_calculator import MetricsCalculator
from notification import send_notification
from optimized_resource_manager import OptimizedResourceManager
from performance_cache import initialize_global_cache, cleanup_global_cache, cached


class AsyncScalingOrchestrator:
    """High-performance async orchestrator for LXC container scaling operations."""
    
    def __init__(self, max_concurrent_containers: int = 20):
        """Initialize the async scaling orchestrator.
        
        Args:
            max_concurrent_containers: Maximum number of containers to process concurrently
        """
        self.config_manager = config_manager
        self.max_concurrent_containers = max_concurrent_containers
        
        # Initialize async components
        self.async_executor = AsyncCommandExecutor(
            self.config_manager, 
            max_concurrent_commands=max_concurrent_containers
        )
        self.metrics_calculator = MetricsCalculator(self.config_manager)
        
        # Initialize optimized resource manager
        self.resource_manager = OptimizedResourceManager(
            self.config_manager, self.async_executor, self.metrics_calculator
        )
        
        # Initialize horizontal scaler (if needed)
        self.horizontal_scaler = HorizontalScaler(
            self.config_manager, self.async_executor, self.metrics_calculator
        )
        
        # Performance tracking
        self._cycle_stats = {
            'total_cycles': 0,
            'successful_cycles': 0,
            'failed_cycles': 0,
            'avg_cycle_time': 0.0,
            'containers_processed': 0,
            'optimizations_applied': 0
        }
        
        self._initialized = False
    
    async def initialize(self) -> None:
        """Initialize the async orchestrator and its components."""
        if self._initialized:
            return
        
        try:
            # Initialize global cache system
            await initialize_global_cache()
            
            # Initialize async executor with connection pool
            await self.async_executor.initialize_pool()
            
            self._initialized = True
            logging.info("Async scaling orchestrator initialized successfully")
            
        except Exception as e:
            logging.error(f"Failed to initialize async orchestrator: {e}")
            raise
    
    async def process_scaling_cycle_async(
        self,
        containers_data: Dict[str, Dict[str, Any]],
        energy_mode: bool = False
    ) -> Dict[str, Any]:
        """Process a complete scaling cycle asynchronously with optimizations.
        
        Args:
            containers_data: Dictionary containing container resource usage data
            energy_mode: Flag to enable energy efficiency mode during off-peak hours
            
        Returns:
            Dictionary containing cycle results and performance metrics
        """
        cycle_start_time = time.time()
        cycle_id = f"cycle_{int(cycle_start_time)}"
        
        logging.info(f"Starting async scaling cycle {cycle_id} with {len(containers_data)} containers")
        
        try:
            # Ensure orchestrator is initialized
            if not self._initialized:
                await self.initialize()
            
            # Pre-process container data and filter ignored containers
            active_containers = {
                ctid: data for ctid, data in containers_data.items()
                if not self.config_manager.is_ignored(ctid)
            }
            
            logging.info(f"Processing {len(active_containers)} active containers "
                        f"(filtered {len(containers_data) - len(active_containers)} ignored)")
            
            # Log cycle overview
            await self._log_cycle_overview_async(active_containers, energy_mode, cycle_id)
            
            # Collect performance metrics concurrently
            metrics_task = asyncio.create_task(
                self._collect_performance_metrics_async(active_containers)
            )
            
            # Process resource scaling using optimized algorithms
            resource_results = await self.resource_manager.process_containers_optimized(
                active_containers, energy_mode
            )
            
            # Process horizontal scaling if enabled
            horizontal_results = {}
            scaling_groups = self.config_manager.get_horizontal_scaling_groups()
            if scaling_groups:
                horizontal_task = asyncio.create_task(
                    self._process_horizontal_scaling_async(active_containers)
                )
                horizontal_results = await horizontal_task
            
            # Wait for metrics collection to complete
            await metrics_task
            
            # Calculate cycle performance
            cycle_duration = time.time() - cycle_start_time
            
            # Compile results
            results = {
                'cycle_id': cycle_id,
                'start_time': cycle_start_time,
                'duration': cycle_duration,
                'total_containers': len(containers_data),
                'active_containers': len(active_containers),
                'resource_results': resource_results,
                'horizontal_results': horizontal_results,
                'performance_stats': self._get_performance_summary()
            }
            
            # Log cycle completion
            await self._log_cycle_completion_async(results)
            
            # Update statistics
            self._update_cycle_stats(True, cycle_duration, len(active_containers))
            
            logging.info(f"Async scaling cycle {cycle_id} completed successfully in {cycle_duration:.2f}s")
            
            return results
            
        except Exception as e:
            cycle_duration = time.time() - cycle_start_time
            logging.exception(f"Error during async scaling cycle {cycle_id}: {e}")
            
            # Update error statistics
            self._update_cycle_stats(False, cycle_duration, len(containers_data))
            
            # Send error notification
            await self._send_error_notification_async(cycle_id, str(e))
            
            # Return error result
            return {
                'cycle_id': cycle_id,
                'start_time': cycle_start_time,
                'duration': cycle_duration,
                'error': str(e),
                'success': False
            }
    
    async def _log_cycle_overview_async(
        self,
        containers_data: Dict[str, Dict[str, Any]],
        energy_mode: bool,
        cycle_id: str
    ) -> None:
        """Log overview information for the scaling cycle asynchronously.
        
        Args:
            containers_data: Container resource usage data
            energy_mode: Energy efficiency mode flag
            cycle_id: Unique cycle identifier
        """
        try:
            total_containers = len(containers_data)
            is_off_peak = self.metrics_calculator.is_off_peak()
            behavior_mode = self.config_manager.get_default('behaviour', 'normal')
            behavior_multiplier = self.metrics_calculator.get_behavior_multiplier()
            
            # Get resource utilization statistics
            cpu_usages = [data.get('cpu', 0) for data in containers_data.values()]
            memory_utilizations = []
            
            for data in containers_data.values():
                mem_usage = data.get('mem', 0)
                mem_total = data.get('initial_memory', 1)
                memory_utilizations.append((mem_usage / mem_total) * 100)
            
            overview_data = {
                'cycle_id': cycle_id,
                'timestamp': datetime.now().isoformat(),
                'total_containers': total_containers,
                'energy_mode': energy_mode,
                'off_peak_hours': is_off_peak,
                'behavior_mode': behavior_mode,
                'behavior_multiplier': behavior_multiplier,
                'avg_cpu_usage': round(sum(cpu_usages) / len(cpu_usages), 2) if cpu_usages else 0,
                'avg_memory_utilization': round(sum(memory_utilizations) / len(memory_utilizations), 2) if memory_utilizations else 0,
                'max_cpu_usage': round(max(cpu_usages), 2) if cpu_usages else 0,
                'max_memory_utilization': round(max(memory_utilizations), 2) if memory_utilizations else 0
            }
            
            logging.info(f"Cycle overview: {overview_data}")
            
            # Log horizontal scaling groups
            scaling_groups = self.config_manager.get_horizontal_scaling_groups()
            if scaling_groups:
                logging.info(f"Active horizontal scaling groups: {len(scaling_groups)}")
                for group_name, group_config in scaling_groups.items():
                    containers_in_group = len(group_config.get('lxc_containers', []))
                    logging.info(f"  {group_name}: {containers_in_group} containers")
            
        except Exception as e:
            logging.error(f"Error logging cycle overview: {e}")
    
    async def _collect_performance_metrics_async(self, containers_data: Dict[str, Dict[str, Any]]) -> None:
        """Collect and log performance metrics asynchronously.
        
        Args:
            containers_data: Container resource usage data
        """
        try:
            # Process metrics collection concurrently for better performance
            tasks = []
            
            for ctid, usage_data in containers_data.items():
                task = asyncio.create_task(
                    self._process_container_metrics(ctid, usage_data)
                )
                tasks.append(task)
            
            # Wait for all metrics to be collected
            await asyncio.gather(*tasks, return_exceptions=True)
            
        except Exception as e:
            logging.error(f"Error collecting performance metrics: {e}")
    
    async def _process_container_metrics(self, ctid: str, usage_data: Dict[str, Any]) -> None:
        """Process metrics for a single container.
        
        Args:
            ctid: Container ID
            usage_data: Container usage data
        """
        try:
            # Calculate utilization metrics
            utilization_metrics = self.metrics_calculator.calculate_resource_utilization({ctid: usage_data})
            container_metrics = utilization_metrics.get(ctid, {})
            
            # Add timestamp and container info
            metrics = {
                'timestamp': datetime.now().isoformat(),
                'container_id': ctid,
                **container_metrics,
                'tier_name': self.config_manager.get_tier_config(ctid).get('tier_name', 'default')
            }
            
            # Log performance metrics asynchronously
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: safe_execute(
                    log_json_event,
                    ctid, 'performance_metrics', metrics,
                    log_errors=True
                )
            )
            
        except Exception as e:
            logging.error(f"Error processing metrics for container {ctid}: {e}")
    
    async def _process_horizontal_scaling_async(self, containers_data: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Process horizontal scaling asynchronously.
        
        Args:
            containers_data: Container resource usage data
            
        Returns:
            Dictionary with horizontal scaling results
        """
        try:
            # Run horizontal scaling in executor to avoid blocking
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self.horizontal_scaler.manage_horizontal_scaling(containers_data)
            )
            
            return {'success': True, 'result': result}
            
        except Exception as e:
            logging.error(f"Error in horizontal scaling: {e}")
            return {'success': False, 'error': str(e)}
    
    async def _log_cycle_completion_async(self, results: Dict[str, Any]) -> None:
        """Log cycle completion information asynchronously.
        
        Args:
            results: Cycle results dictionary
        """
        try:
            summary_data = {
                'cycle_id': results['cycle_id'],
                'timestamp': datetime.now().isoformat(),
                'duration': round(results['duration'], 2),
                'total_containers': results['total_containers'],
                'active_containers': results['active_containers'],
                'successful_operations': results['resource_results'].get('successful_operations', 0),
                'failed_operations': results['resource_results'].get('failed_operations', 0),
                'performance_summary': results['performance_stats']
            }
            
            # Log the summary asynchronously
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: safe_execute(
                    log_json_event,
                    'system', 'async_scaling_cycle_summary', summary_data,
                    log_errors=True
                )
            )
            
            logging.info(f"Async scaling cycle summary: {summary_data}")
            
        except Exception as e:
            logging.error(f"Error logging cycle completion: {e}")
    
    async def _send_error_notification_async(self, cycle_id: str, error_message: str) -> None:
        """Send error notification asynchronously.
        
        Args:
            cycle_id: Cycle identifier
            error_message: Error message
        """
        try:
            # Send notification in executor to avoid blocking
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: safe_execute(
                    send_notification,
                    f"Async Scaling Cycle Error - {cycle_id}",
                    f"An error occurred during the async scaling cycle: {error_message}",
                    priority=8,
                    log_errors=True
                )
            )
            
        except Exception as e:
            logging.error(f"Error sending error notification: {e}")
    
    def _get_performance_summary(self) -> Dict[str, Any]:
        """Get performance summary from all components."""
        try:
            return {
                'orchestrator_stats': self._cycle_stats.copy(),
                'executor_stats': self.async_executor.get_performance_stats(),
                'resource_manager_stats': self.resource_manager.get_performance_stats()
            }
        except Exception as e:
            logging.error(f"Error getting performance summary: {e}")
            return {}
    
    def _update_cycle_stats(self, success: bool, duration: float, containers_processed: int) -> None:
        """Update cycle statistics.
        
        Args:
            success: Whether the cycle was successful
            duration: Cycle duration in seconds
            containers_processed: Number of containers processed
        """
        self._cycle_stats['total_cycles'] += 1
        self._cycle_stats['containers_processed'] += containers_processed
        
        if success:
            self._cycle_stats['successful_cycles'] += 1
        else:
            self._cycle_stats['failed_cycles'] += 1
        
        # Update average cycle time
        total_cycles = self._cycle_stats['total_cycles']
        current_avg = self._cycle_stats['avg_cycle_time']
        self._cycle_stats['avg_cycle_time'] = (
            (current_avg * (total_cycles - 1) + duration) / total_cycles
        )
    
    @cached(ttl=30.0, key_prefix="system_readiness_")
    async def validate_system_readiness_async(self) -> bool:
        """Validate that the system is ready for scaling operations asynchronously.
        
        Returns:
            True if system is ready, False otherwise
        """
        try:
            # Test async command executor
            test_result = await self.async_executor.execute('echo "async_test"')
            if test_result != "async_test":
                logging.error("Async command executor validation failed")
                return False
            
            # Validate configuration
            required_defaults = ['reserve_cpu_percent', 'reserve_memory_mb']
            for key in required_defaults:
                if self.config_manager.get_default(key) is None:
                    logging.error(f"Missing required configuration: {key}")
                    return False
            
            # Test if we can get basic system information
            available_cores, available_memory = await self.resource_manager.get_available_resources()
            
            if available_cores <= 0 or available_memory <= 0:
                logging.error("Unable to retrieve system resource information")
                return False
            
            logging.info("Async system readiness validation passed")
            return True
            
        except Exception as e:
            logging.error(f"Async system readiness validation failed: {e}")
            return False
    
    async def cleanup(self) -> None:
        """Cleanup resources used by the orchestrator."""
        try:
            # Cleanup all async components
            await self.async_executor.cleanup()
            await self.resource_manager.cleanup()
            await cleanup_global_cache()
            
            self._initialized = False
            logging.info("Async scaling orchestrator cleanup completed")
            
        except Exception as e:
            logging.error(f"Error during async orchestrator cleanup: {e}")
    
    @asynccontextmanager
    async def scaling_session(self):
        """Async context manager for scaling sessions."""
        try:
            await self.initialize()
            yield self
        finally:
            await self.cleanup()
    
    def get_performance_statistics(self) -> Dict[str, Any]:
        """Get comprehensive performance statistics."""
        stats = self._get_performance_summary()
        
        # Add success rates
        if self._cycle_stats['total_cycles'] > 0:
            stats['orchestrator_stats']['success_rate'] = (
                self._cycle_stats['successful_cycles'] / self._cycle_stats['total_cycles']
            ) * 100
        else:
            stats['orchestrator_stats']['success_rate'] = 0.0
        
        return stats