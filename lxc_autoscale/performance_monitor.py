"""Comprehensive performance monitoring and metrics collection system."""

import asyncio
import time
import logging
import psutil
from typing import Any, Dict, List, Optional, Callable, Union
from dataclasses import dataclass, field, asdict
from collections import defaultdict, deque
import threading
import json
import os
from datetime import datetime, timedelta
import statistics

from memory_optimizer import get_memory_profiler
from circuit_breaker import get_circuit_breaker_manager
from advanced_error_recovery import get_error_recovery_manager


@dataclass
class PerformanceMetric:
    """Individual performance metric."""
    name: str
    value: Union[int, float]
    unit: str
    timestamp: float
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class SystemMetrics:
    """System-level metrics."""
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_available_mb: float
    disk_usage_percent: float
    network_bytes_sent: int
    network_bytes_recv: int
    load_average: List[float]
    process_count: int
    thread_count: int
    file_descriptors: int
    timestamp: float


@dataclass
class ApplicationMetrics:
    """Application-specific metrics."""
    scaling_operations_total: int
    scaling_operations_successful: int
    scaling_operations_failed: int
    containers_processed: int
    avg_processing_time_ms: float
    cache_hit_rate: float
    memory_usage_mb: float
    active_connections: int
    circuit_breakers_open: int
    error_recovery_rate: float
    timestamp: float


class MetricsCollector:
    """Collects and aggregates performance metrics."""
    
    def __init__(self, max_history: int = 1000):
        """Initialize metrics collector.
        
        Args:
            max_history: Maximum number of metric points to keep in memory
        """
        self.max_history = max_history
        self.metrics_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_history))
        self.system_metrics_history: deque = deque(maxlen=max_history)
        self.app_metrics_history: deque = deque(maxlen=max_history)
        self._lock = threading.RLock()
        
        # Performance counters
        self.counters = defaultdict(int)
        self.timers = defaultdict(list)
        self.gauges = defaultdict(float)
        
        # Process and system info
        self.process = psutil.Process()
        self.start_time = time.time()
    
    def record_metric(self, metric: PerformanceMetric) -> None:
        """Record a performance metric.
        
        Args:
            metric: Performance metric to record
        """
        with self._lock:
            self.metrics_history[metric.name].append(metric)
    
    def increment_counter(self, name: str, value: int = 1, tags: Dict[str, str] = None) -> None:
        """Increment a counter metric.
        
        Args:
            name: Counter name
            value: Value to increment by
            tags: Optional tags for the metric
        """
        key = f"{name}:{json.dumps(tags or {}, sort_keys=True)}"
        with self._lock:
            self.counters[key] += value
    
    def record_timer(self, name: str, duration_ms: float, tags: Dict[str, str] = None) -> None:
        """Record a timing metric.
        
        Args:
            name: Timer name
            duration_ms: Duration in milliseconds
            tags: Optional tags for the metric
        """
        key = f"{name}:{json.dumps(tags or {}, sort_keys=True)}"
        with self._lock:
            self.timers[key].append(duration_ms)
            # Keep only recent measurements
            if len(self.timers[key]) > 100:
                self.timers[key] = self.timers[key][-100:]
    
    def set_gauge(self, name: str, value: float, tags: Dict[str, str] = None) -> None:
        """Set a gauge metric value.
        
        Args:
            name: Gauge name
            value: Current value
            tags: Optional tags for the metric
        """
        key = f"{name}:{json.dumps(tags or {}, sort_keys=True)}"
        with self._lock:
            self.gauges[key] = value
    
    def collect_system_metrics(self) -> SystemMetrics:
        """Collect current system metrics.
        
        Returns:
            System metrics snapshot
        """
        try:
            # CPU and memory
            cpu_percent = psutil.cpu_percent(interval=None)
            memory = psutil.virtual_memory()
            
            # Disk usage for root partition
            disk = psutil.disk_usage('/')
            
            # Network stats
            net_io = psutil.net_io_counters()
            
            # Load average (Unix systems)
            try:
                load_avg = list(psutil.getloadavg())
            except AttributeError:
                load_avg = [0.0, 0.0, 0.0]  # Windows doesn't have load average
            
            # Process info
            try:
                process_count = len(psutil.pids())
                thread_count = self.process.num_threads()
                
                # File descriptors (Unix systems)
                try:
                    file_descriptors = self.process.num_fds()
                except AttributeError:
                    file_descriptors = 0  # Windows doesn't have file descriptors
                    
            except Exception:
                process_count = 0
                thread_count = 0
                file_descriptors = 0
            
            metrics = SystemMetrics(
                cpu_percent=cpu_percent,
                memory_percent=memory.percent,
                memory_used_mb=memory.used / 1024 / 1024,
                memory_available_mb=memory.available / 1024 / 1024,
                disk_usage_percent=disk.percent,
                network_bytes_sent=net_io.bytes_sent,
                network_bytes_recv=net_io.bytes_recv,
                load_average=load_avg,
                process_count=process_count,
                thread_count=thread_count,
                file_descriptors=file_descriptors,
                timestamp=time.time()
            )
            
            with self._lock:
                self.system_metrics_history.append(metrics)
            
            return metrics
            
        except Exception as e:
            logging.error(f"Error collecting system metrics: {e}")
            return SystemMetrics(
                cpu_percent=0.0, memory_percent=0.0, memory_used_mb=0.0,
                memory_available_mb=0.0, disk_usage_percent=0.0,
                network_bytes_sent=0, network_bytes_recv=0,
                load_average=[0.0, 0.0, 0.0], process_count=0,
                thread_count=0, file_descriptors=0, timestamp=time.time()
            )
    
    def collect_application_metrics(self) -> ApplicationMetrics:
        """Collect current application metrics.
        
        Returns:
            Application metrics snapshot
        """
        try:
            # Get memory profiler stats
            memory_profiler = get_memory_profiler()
            memory_stats = memory_profiler.get_current_stats()
            
            # Get circuit breaker stats
            cb_manager = get_circuit_breaker_manager()
            cb_stats = cb_manager.get_all_stats()
            cb_open = len(cb_manager.get_unhealthy_breakers())
            
            # Get error recovery stats
            recovery_manager = get_error_recovery_manager()
            recovery_stats = recovery_manager.get_recovery_stats()
            
            # Calculate application-specific metrics
            total_ops = recovery_stats.get('total_operations', 0)
            successful_ops = recovery_stats.get('successful_operations', 0)
            failed_ops = recovery_stats.get('failed_operations', 0)
            
            # Calculate average processing time from timers
            avg_processing_time = 0.0
            processing_times = []
            for key, times in self.timers.items():
                if 'processing' in key.lower() or 'scaling' in key.lower():
                    processing_times.extend(times)
            
            if processing_times:
                avg_processing_time = statistics.mean(processing_times)
            
            # Get cache stats if available
            cache_hit_rate = 0.0
            try:
                from performance_cache import get_global_cache
                cache = get_global_cache()
                cache_stats = cache.get_stats()
                if cache_stats['total_requests'] > 0:
                    cache_hit_rate = cache_stats['hit_rate']
            except Exception:
                pass
            
            metrics = ApplicationMetrics(
                scaling_operations_total=total_ops,
                scaling_operations_successful=successful_ops,
                scaling_operations_failed=failed_ops,
                containers_processed=self.counters.get('containers_processed', 0),
                avg_processing_time_ms=avg_processing_time,
                cache_hit_rate=cache_hit_rate,
                memory_usage_mb=memory_stats.current_memory_mb,
                active_connections=self.gauges.get('active_connections', 0),
                circuit_breakers_open=cb_open,
                error_recovery_rate=recovery_stats.get('success_rate', 0.0),
                timestamp=time.time()
            )
            
            with self._lock:
                self.app_metrics_history.append(metrics)
            
            return metrics
            
        except Exception as e:
            logging.error(f"Error collecting application metrics: {e}")
            return ApplicationMetrics(
                scaling_operations_total=0, scaling_operations_successful=0,
                scaling_operations_failed=0, containers_processed=0,
                avg_processing_time_ms=0.0, cache_hit_rate=0.0,
                memory_usage_mb=0.0, active_connections=0,
                circuit_breakers_open=0, error_recovery_rate=0.0,
                timestamp=time.time()
            )
    
    def get_metric_summary(self, metric_name: str, duration_minutes: int = 5) -> Dict[str, float]:
        """Get summary statistics for a metric over a time period.
        
        Args:
            metric_name: Name of the metric
            duration_minutes: Time period to analyze
            
        Returns:
            Dictionary with metric statistics
        """
        cutoff_time = time.time() - (duration_minutes * 60)
        
        with self._lock:
            if metric_name not in self.metrics_history:
                return {}
            
            recent_metrics = [
                m for m in self.metrics_history[metric_name]
                if m.timestamp >= cutoff_time
            ]
            
            if not recent_metrics:
                return {}
            
            values = [m.value for m in recent_metrics]
            
            return {
                'count': len(values),
                'min': min(values),
                'max': max(values),
                'avg': statistics.mean(values),
                'median': statistics.median(values),
                'std_dev': statistics.stdev(values) if len(values) > 1 else 0.0,
                'latest': values[-1]
            }
    
    def get_performance_report(self) -> Dict[str, Any]:
        """Generate comprehensive performance report.
        
        Returns:
            Performance report dictionary
        """
        current_time = time.time()
        uptime_hours = (current_time - self.start_time) / 3600
        
        # Get latest metrics
        system_metrics = self.collect_system_metrics()
        app_metrics = self.collect_application_metrics()
        
        # Calculate rates and trends
        report = {
            'timestamp': current_time,
            'uptime_hours': uptime_hours,
            'system_metrics': asdict(system_metrics),
            'application_metrics': asdict(app_metrics),
            'counters': dict(self.counters),
            'gauges': dict(self.gauges),
            'performance_summary': {}
        }
        
        # Add timer summaries
        timer_summaries = {}
        for timer_name, times in self.timers.items():
            if times:
                timer_summaries[timer_name] = {
                    'count': len(times),
                    'avg_ms': statistics.mean(times),
                    'min_ms': min(times),
                    'max_ms': max(times),
                    'p95_ms': self._calculate_percentile(times, 95),
                    'p99_ms': self._calculate_percentile(times, 99)
                }
        
        report['timer_summaries'] = timer_summaries
        
        # Add trend analysis
        if len(self.system_metrics_history) > 1:
            report['trends'] = self._calculate_trends()
        
        return report
    
    def _calculate_percentile(self, values: List[float], percentile: float) -> float:
        """Calculate percentile value.
        
        Args:
            values: List of values
            percentile: Percentile to calculate (0-100)
            
        Returns:
            Percentile value
        """
        if not values:
            return 0.0
        
        sorted_values = sorted(values)
        index = (percentile / 100) * (len(sorted_values) - 1)
        
        if index.is_integer():
            return sorted_values[int(index)]
        else:
            lower = sorted_values[int(index)]
            upper = sorted_values[int(index) + 1]
            return lower + (upper - lower) * (index - int(index))
    
    def _calculate_trends(self) -> Dict[str, Any]:
        """Calculate performance trends.
        
        Returns:
            Trends dictionary
        """
        try:
            # Get recent system metrics
            recent_count = min(10, len(self.system_metrics_history))
            recent_metrics = list(self.system_metrics_history)[-recent_count:]
            
            if len(recent_metrics) < 2:
                return {}
            
            # Calculate trends
            cpu_values = [m.cpu_percent for m in recent_metrics]
            memory_values = [m.memory_percent for m in recent_metrics]
            
            return {
                'cpu_trend': self._calculate_trend(cpu_values),
                'memory_trend': self._calculate_trend(memory_values),
                'samples': len(recent_metrics)
            }
            
        except Exception as e:
            logging.error(f"Error calculating trends: {e}")
            return {}
    
    def _calculate_trend(self, values: List[float]) -> str:
        """Calculate trend direction for a series of values.
        
        Args:
            values: List of numeric values
            
        Returns:
            Trend direction: 'increasing', 'decreasing', or 'stable'
        """
        if len(values) < 2:
            return 'stable'
        
        # Simple linear trend calculation
        increases = 0
        decreases = 0
        
        for i in range(1, len(values)):
            diff = values[i] - values[i-1]
            if abs(diff) > 1.0:  # Threshold for significant change
                if diff > 0:
                    increases += 1
                else:
                    decreases += 1
        
        if increases > decreases:
            return 'increasing'
        elif decreases > increases:
            return 'decreasing'
        else:
            return 'stable'


class PerformanceMonitor:
    """Main performance monitoring service."""
    
    def __init__(self, collection_interval: float = 30.0):
        """Initialize performance monitor.
        
        Args:
            collection_interval: Metrics collection interval in seconds
        """
        self.collection_interval = collection_interval
        self.collector = MetricsCollector()
        self.running = False
        self._monitor_task: Optional[asyncio.Task] = None
        
        # Alert thresholds
        self.alert_thresholds = {
            'cpu_percent': 90.0,
            'memory_percent': 90.0,
            'disk_usage_percent': 95.0,
            'error_rate': 10.0,  # Percent
            'response_time_ms': 5000.0
        }
    
    async def start_monitoring(self) -> None:
        """Start the performance monitoring service."""
        if self.running:
            return
        
        self.running = True
        self._monitor_task = asyncio.create_task(self._monitoring_loop())
        logging.info(f"Performance monitoring started (interval: {self.collection_interval}s)")
    
    async def stop_monitoring(self) -> None:
        """Stop the performance monitoring service."""
        self.running = False
        
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        
        logging.info("Performance monitoring stopped")
    
    async def _monitoring_loop(self) -> None:
        """Main monitoring loop."""
        try:
            while self.running:
                try:
                    # Collect metrics
                    system_metrics = self.collector.collect_system_metrics()
                    app_metrics = self.collector.collect_application_metrics()
                    
                    # Check for alerts
                    await self._check_alerts(system_metrics, app_metrics)
                    
                    # Wait for next collection
                    await asyncio.sleep(self.collection_interval)
                    
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logging.error(f"Error in monitoring loop: {e}")
                    await asyncio.sleep(self.collection_interval)
                    
        except asyncio.CancelledError:
            pass
        finally:
            logging.info("Performance monitoring loop ended")
    
    async def _check_alerts(self, system_metrics: SystemMetrics, app_metrics: ApplicationMetrics) -> None:
        """Check metrics against alert thresholds.
        
        Args:
            system_metrics: Current system metrics
            app_metrics: Current application metrics
        """
        try:
            alerts = []
            
            # Check system thresholds
            if system_metrics.cpu_percent > self.alert_thresholds['cpu_percent']:
                alerts.append(f"High CPU usage: {system_metrics.cpu_percent:.1f}%")
            
            if system_metrics.memory_percent > self.alert_thresholds['memory_percent']:
                alerts.append(f"High memory usage: {system_metrics.memory_percent:.1f}%")
            
            if system_metrics.disk_usage_percent > self.alert_thresholds['disk_usage_percent']:
                alerts.append(f"High disk usage: {system_metrics.disk_usage_percent:.1f}%")
            
            # Check application thresholds
            if app_metrics.scaling_operations_total > 0:
                error_rate = (app_metrics.scaling_operations_failed / app_metrics.scaling_operations_total) * 100
                if error_rate > self.alert_thresholds['error_rate']:
                    alerts.append(f"High error rate: {error_rate:.1f}%")
            
            if app_metrics.avg_processing_time_ms > self.alert_thresholds['response_time_ms']:
                alerts.append(f"High response time: {app_metrics.avg_processing_time_ms:.1f}ms")
            
            # Send alerts if any
            if alerts:
                await self._send_performance_alert(alerts)
                
        except Exception as e:
            logging.error(f"Error checking alerts: {e}")
    
    async def _send_performance_alert(self, alerts: List[str]) -> None:
        """Send performance alert notification.
        
        Args:
            alerts: List of alert messages
        """
        try:
            from notification import send_notification
            
            message = "Performance Alert:\n" + "\n".join(f"• {alert}" for alert in alerts)
            
            # Send notification in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: send_notification(
                    "Performance Alert",
                    message,
                    priority=7
                )
            )
            
        except Exception as e:
            logging.error(f"Error sending performance alert: {e}")
    
    def get_collector(self) -> MetricsCollector:
        """Get the metrics collector instance."""
        return self.collector
    
    def set_alert_threshold(self, metric: str, threshold: float) -> None:
        """Set alert threshold for a metric.
        
        Args:
            metric: Metric name
            threshold: Alert threshold value
        """
        self.alert_thresholds[metric] = threshold
        logging.info(f"Set alert threshold for {metric}: {threshold}")


# Global performance monitor instance
_global_monitor = PerformanceMonitor()


def get_performance_monitor() -> PerformanceMonitor:
    """Get the global performance monitor."""
    return _global_monitor


def performance_timer(operation_name: str):
    """Decorator for timing operations.
    
    Args:
        operation_name: Name of the operation being timed
    """
    def decorator(func):
        if asyncio.iscoroutinefunction(func):
            async def async_wrapper(*args, **kwargs):
                start_time = time.time()
                try:
                    result = await func(*args, **kwargs)
                    return result
                finally:
                    duration_ms = (time.time() - start_time) * 1000
                    _global_monitor.collector.record_timer(operation_name, duration_ms)
            return async_wrapper
        else:
            def sync_wrapper(*args, **kwargs):
                start_time = time.time()
                try:
                    result = func(*args, **kwargs)
                    return result
                finally:
                    duration_ms = (time.time() - start_time) * 1000
                    _global_monitor.collector.record_timer(operation_name, duration_ms)
            return sync_wrapper
    
    return decorator