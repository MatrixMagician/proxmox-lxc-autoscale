"""Memory optimization and profiling utilities."""

import gc
import sys
import os
import psutil
import logging
import asyncio
import weakref
from typing import Any, Dict, List, Optional, Callable, Set
from dataclasses import dataclass, field
from contextlib import contextmanager
import tracemalloc
import linecache
import threading
import time
from collections import defaultdict


@dataclass
class MemorySnapshot:
    """Memory usage snapshot."""
    timestamp: float
    process_memory_mb: float
    system_memory_percent: float
    gc_objects: int
    gc_collections: List[int]
    top_allocations: List[tuple] = field(default_factory=list)


@dataclass
class MemoryStats:
    """Memory statistics and metrics."""
    peak_memory_mb: float = 0.0
    current_memory_mb: float = 0.0
    memory_growth_mb: float = 0.0
    gc_collections_total: int = 0
    objects_tracked: int = 0
    leaked_objects: int = 0
    allocation_hotspots: Dict[str, int] = field(default_factory=dict)


class MemoryProfiler:
    """Advanced memory profiler for tracking usage and leaks."""
    
    def __init__(self, enable_tracemalloc: bool = True):
        """Initialize memory profiler.
        
        Args:
            enable_tracemalloc: Enable Python's tracemalloc for detailed tracking
        """
        self.enable_tracemalloc = enable_tracemalloc
        self.process = psutil.Process()
        self.snapshots: List[MemorySnapshot] = []
        self.baseline_memory = 0.0
        self.peak_memory = 0.0
        self._tracking_enabled = False
        self._tracked_objects: Set[weakref.ref] = set()
        self._lock = threading.RLock()
        
        if enable_tracemalloc and not tracemalloc.is_tracing():
            tracemalloc.start(10)  # Keep top 10 frames
            logging.info("Memory profiler initialized with tracemalloc")
        else:
            logging.info("Memory profiler initialized without tracemalloc")
    
    def start_tracking(self) -> None:
        """Start memory tracking."""
        with self._lock:
            self._tracking_enabled = True
            self.baseline_memory = self._get_current_memory()
            self.peak_memory = self.baseline_memory
            
            # Take initial snapshot
            self._take_snapshot()
            logging.info(f"Memory tracking started. Baseline: {self.baseline_memory:.2f} MB")
    
    def stop_tracking(self) -> None:
        """Stop memory tracking."""
        with self._lock:
            self._tracking_enabled = False
            
            # Take final snapshot
            if self.snapshots:
                self._take_snapshot()
            
            logging.info("Memory tracking stopped")
    
    def _get_current_memory(self) -> float:
        """Get current process memory usage in MB."""
        try:
            memory_info = self.process.memory_info()
            return memory_info.rss / 1024 / 1024  # Convert to MB
        except Exception as e:
            logging.error(f"Error getting memory info: {e}")
            return 0.0
    
    def _take_snapshot(self) -> MemorySnapshot:
        """Take a memory usage snapshot."""
        try:
            current_memory = self._get_current_memory()
            system_memory = psutil.virtual_memory().percent
            gc_objects = len(gc.get_objects())
            gc_stats = gc.get_stats()
            gc_collections = [stat['collections'] for stat in gc_stats]
            
            # Update peak memory
            if current_memory > self.peak_memory:
                self.peak_memory = current_memory
            
            # Get top allocations if tracemalloc is enabled
            top_allocations = []
            if self.enable_tracemalloc and tracemalloc.is_tracing():
                snapshot = tracemalloc.take_snapshot()
                top_stats = snapshot.statistics('lineno')[:10]  # Top 10
                
                for stat in top_stats:
                    filename = stat.traceback.format()[-1]
                    top_allocations.append((
                        filename,
                        stat.size / 1024 / 1024,  # Size in MB
                        stat.count
                    ))
            
            snapshot = MemorySnapshot(
                timestamp=time.time(),
                process_memory_mb=current_memory,
                system_memory_percent=system_memory,
                gc_objects=gc_objects,
                gc_collections=gc_collections,
                top_allocations=top_allocations
            )
            
            if self._tracking_enabled:
                self.snapshots.append(snapshot)
            
            return snapshot
            
        except Exception as e:
            logging.error(f"Error taking memory snapshot: {e}")
            return MemorySnapshot(
                timestamp=time.time(),
                process_memory_mb=0.0,
                system_memory_percent=0.0,
                gc_objects=0,
                gc_collections=[]
            )
    
    def get_current_stats(self) -> MemoryStats:
        """Get current memory statistics."""
        current_memory = self._get_current_memory()
        memory_growth = current_memory - self.baseline_memory
        
        # Get GC statistics
        gc_stats = gc.get_stats()
        total_collections = sum(stat['collections'] for stat in gc_stats)
        
        # Count tracked objects
        # Clean up dead references
        self._tracked_objects = {ref for ref in self._tracked_objects if ref() is not None}
        
        # Get allocation hotspots
        hotspots = {}
        if self.enable_tracemalloc and tracemalloc.is_tracing():
            snapshot = tracemalloc.take_snapshot()
            top_stats = snapshot.statistics('filename')[:5]
            
            for stat in top_stats:
                filename = os.path.basename(stat.traceback.format()[-1].split(':')[0])
                hotspots[filename] = stat.size // 1024  # Size in KB
        
        return MemoryStats(
            peak_memory_mb=self.peak_memory,
            current_memory_mb=current_memory,
            memory_growth_mb=memory_growth,
            gc_collections_total=total_collections,
            objects_tracked=len(self._tracked_objects),
            leaked_objects=self._count_potential_leaks(),
            allocation_hotspots=hotspots
        )
    
    def _count_potential_leaks(self) -> int:
        """Count potential memory leaks based on object growth."""
        if len(self.snapshots) < 2:
            return 0
        
        # Compare object counts between first and last snapshots
        first_snapshot = self.snapshots[0]
        last_snapshot = self.snapshots[-1]
        
        object_growth = last_snapshot.gc_objects - first_snapshot.gc_objects
        
        # Threshold for considering objects as potential leaks
        # This is a heuristic - significant growth might indicate leaks
        leak_threshold = max(1000, first_snapshot.gc_objects * 0.1)  # 10% growth or 1000 objects
        
        return max(0, object_growth - leak_threshold)
    
    def track_object(self, obj: Any) -> None:
        """Track a specific object for memory leak detection.
        
        Args:
            obj: Object to track
        """
        try:
            ref = weakref.ref(obj)
            self._tracked_objects.add(ref)
        except TypeError:
            # Object doesn't support weak references
            pass
    
    def force_garbage_collection(self) -> Dict[str, int]:
        """Force garbage collection and return statistics.
        
        Returns:
            Dictionary with GC statistics
        """
        before_objects = len(gc.get_objects())
        
        # Force collection for all generations
        collected = []
        for generation in range(3):
            collected.append(gc.collect(generation))
        
        after_objects = len(gc.get_objects())
        objects_freed = before_objects - after_objects
        
        stats = {
            'objects_before': before_objects,
            'objects_after': after_objects,
            'objects_freed': objects_freed,
            'collected_gen0': collected[0],
            'collected_gen1': collected[1],
            'collected_gen2': collected[2]
        }
        
        logging.info(f"Forced GC: freed {objects_freed} objects")
        return stats
    
    def get_memory_report(self) -> str:
        """Generate a comprehensive memory report.
        
        Returns:
            Formatted memory report string
        """
        stats = self.get_current_stats()
        
        report = [
            "Memory Usage Report",
            "=" * 50,
            f"Current Memory: {stats.current_memory_mb:.2f} MB",
            f"Peak Memory: {stats.peak_memory_mb:.2f} MB",
            f"Memory Growth: {stats.memory_growth_mb:.2f} MB",
            f"GC Collections: {stats.gc_collections_total}",
            f"Objects Tracked: {stats.objects_tracked}",
            f"Potential Leaks: {stats.leaked_objects}",
            "",
            "Allocation Hotspots:",
        ]
        
        for filename, size_kb in stats.allocation_hotspots.items():
            report.append(f"  {filename}: {size_kb} KB")
        
        if self.snapshots:
            report.extend([
                "",
                f"Snapshots Taken: {len(self.snapshots)}",
                f"Tracking Duration: {self.snapshots[-1].timestamp - self.snapshots[0].timestamp:.1f}s"
            ])
        
        return "\n".join(report)
    
    @contextmanager
    def memory_tracking_context(self):
        """Context manager for automatic memory tracking."""
        self.start_tracking()
        try:
            yield self
        finally:
            self.stop_tracking()
    
    def get_top_memory_consumers(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top memory consuming code locations.
        
        Args:
            limit: Maximum number of results to return
            
        Returns:
            List of memory consumption data
        """
        if not (self.enable_tracemalloc and tracemalloc.is_tracing()):
            return []
        
        try:
            snapshot = tracemalloc.take_snapshot()
            top_stats = snapshot.statistics('lineno')[:limit]
            
            results = []
            for stat in top_stats:
                # Get the line content
                frame = stat.traceback.format()[-1]
                filename, line_no = frame.split(':')[:2]
                
                try:
                    line_content = linecache.getline(filename, int(line_no)).strip()
                except:
                    line_content = "Unable to read line"
                
                results.append({
                    'filename': os.path.basename(filename),
                    'line_number': line_no,
                    'line_content': line_content,
                    'size_mb': stat.size / 1024 / 1024,
                    'count': stat.count
                })
            
            return results
            
        except Exception as e:
            logging.error(f"Error getting memory consumers: {e}")
            return []


class MemoryOptimizer:
    """Memory optimizer with automatic optimization strategies."""
    
    def __init__(self, profiler: MemoryProfiler):
        """Initialize memory optimizer.
        
        Args:
            profiler: Memory profiler instance
        """
        self.profiler = profiler
        self.optimization_enabled = True
        self.auto_gc_threshold = 100.0  # MB
        self.optimization_stats = {
            'optimizations_performed': 0,
            'memory_freed_mb': 0.0,
            'auto_gc_triggers': 0
        }
    
    async def optimize_memory_usage(self) -> Dict[str, Any]:
        """Perform memory optimization.
        
        Returns:
            Dictionary with optimization results
        """
        if not self.optimization_enabled:
            return {'enabled': False}
        
        start_memory = self.profiler._get_current_memory()
        optimizations_applied = []
        
        try:
            # Force garbage collection
            gc_stats = self.profiler.force_garbage_collection()
            optimizations_applied.append('garbage_collection')
            
            # Clear internal caches
            self._clear_internal_caches()
            optimizations_applied.append('cache_clearing')
            
            # Optimize data structures
            await self._optimize_data_structures()
            optimizations_applied.append('data_structure_optimization')
            
            # Final memory measurement
            end_memory = self.profiler._get_current_memory()
            memory_freed = start_memory - end_memory
            
            # Update statistics
            self.optimization_stats['optimizations_performed'] += 1
            self.optimization_stats['memory_freed_mb'] += memory_freed
            
            result = {
                'enabled': True,
                'memory_before_mb': start_memory,
                'memory_after_mb': end_memory,
                'memory_freed_mb': memory_freed,
                'optimizations_applied': optimizations_applied,
                'gc_stats': gc_stats
            }
            
            logging.info(f"Memory optimization completed: freed {memory_freed:.2f} MB")
            return result
            
        except Exception as e:
            logging.error(f"Error during memory optimization: {e}")
            return {
                'enabled': True,
                'error': str(e),
                'optimizations_applied': optimizations_applied
            }
    
    def _clear_internal_caches(self) -> None:
        """Clear internal caches to free memory."""
        try:
            # Clear performance cache if available
            from performance_cache import get_global_cache
            cache = get_global_cache()
            cache.clear()
            
            # Clear linecache
            linecache.clearcache()
            
            # Clear sys modules cache
            if hasattr(sys, '_clear_type_cache'):
                sys._clear_type_cache()
            
        except Exception as e:
            logging.error(f"Error clearing internal caches: {e}")
    
    async def _optimize_data_structures(self) -> None:
        """Optimize data structures in memory."""
        try:
            # This is a placeholder for data structure optimizations
            # In practice, this would analyze and optimize specific data structures
            
            # Example optimizations:
            # - Convert large lists to generators where possible
            # - Use __slots__ for classes with many instances
            # - Compress stored data
            # - Remove duplicate objects
            
            await asyncio.sleep(0.01)  # Small delay to yield control
            
        except Exception as e:
            logging.error(f"Error optimizing data structures: {e}")
    
    async def monitor_and_optimize(self, check_interval: float = 60.0) -> None:
        """Monitor memory usage and optimize automatically.
        
        Args:
            check_interval: Interval between checks in seconds
        """
        logging.info(f"Starting automatic memory monitoring (interval: {check_interval}s)")
        
        try:
            while self.optimization_enabled:
                await asyncio.sleep(check_interval)
                
                current_memory = self.profiler._get_current_memory()
                
                if current_memory > self.auto_gc_threshold:
                    logging.info(f"Memory usage ({current_memory:.2f} MB) exceeds threshold "
                               f"({self.auto_gc_threshold:.2f} MB), optimizing...")
                    
                    self.optimization_stats['auto_gc_triggers'] += 1
                    await self.optimize_memory_usage()
                
        except asyncio.CancelledError:
            logging.info("Memory monitoring cancelled")
        except Exception as e:
            logging.error(f"Error in memory monitoring: {e}")
    
    def get_optimization_stats(self) -> Dict[str, Any]:
        """Get memory optimization statistics."""
        return self.optimization_stats.copy()


# Global instances
_global_profiler = MemoryProfiler()
_global_optimizer = MemoryOptimizer(_global_profiler)


def get_memory_profiler() -> MemoryProfiler:
    """Get the global memory profiler."""
    return _global_profiler


def get_memory_optimizer() -> MemoryOptimizer:
    """Get the global memory optimizer."""
    return _global_optimizer


def memory_profile(func: Callable) -> Callable:
    """Decorator for profiling memory usage of functions."""
    def wrapper(*args, **kwargs):
        profiler = get_memory_profiler()
        
        # Take snapshot before
        before_snapshot = profiler._take_snapshot()
        
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            # Take snapshot after
            after_snapshot = profiler._take_snapshot()
            
            # Log memory usage
            memory_diff = after_snapshot.process_memory_mb - before_snapshot.process_memory_mb
            if abs(memory_diff) > 0.1:  # Only log if significant change
                logging.info(f"Function {func.__name__} memory change: {memory_diff:+.2f} MB")
    
    return wrapper


async def optimize_memory_periodically(interval: float = 300.0) -> None:
    """Start periodic memory optimization.
    
    Args:
        interval: Optimization interval in seconds
    """
    optimizer = get_memory_optimizer()
    await optimizer.monitor_and_optimize(interval)