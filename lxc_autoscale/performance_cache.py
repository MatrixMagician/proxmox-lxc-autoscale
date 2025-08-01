"""High-performance caching system for frequently accessed data."""

import asyncio
import time
import logging
from typing import Any, Dict, Optional, Tuple, Callable, Union
from dataclasses import dataclass, field
from functools import wraps
import json
import hashlib
from collections import OrderedDict
import threading
from concurrent.futures import ThreadPoolExecutor


@dataclass
class CacheEntry:
    """Represents a cache entry with metadata."""
    value: Any
    timestamp: float
    ttl: float
    access_count: int = 0
    last_accessed: float = field(default_factory=time.time)
    
    def is_expired(self) -> bool:
        """Check if the cache entry has expired."""
        return time.time() - self.timestamp > self.ttl
    
    def update_access(self) -> None:
        """Update access statistics."""
        self.access_count += 1
        self.last_accessed = time.time()


class PerformanceCache:
    """High-performance multi-level cache with LRU eviction and statistics."""
    
    def __init__(self, max_size: int = 1000, default_ttl: float = 300.0):
        """Initialize the performance cache.
        
        Args:
            max_size: Maximum number of items in the cache
            default_ttl: Default time-to-live in seconds
        """
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        
        # Performance statistics
        self._stats = {
            'hits': 0,
            'misses': 0,
            'evictions': 0,
            'expired_cleanups': 0,
            'total_requests': 0,
            'cache_size': 0,
            'hit_rate': 0.0
        }
        
        # Cleanup task
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False
    
    def start_cleanup_task(self, cleanup_interval: float = 60.0) -> None:
        """Start the background cleanup task.
        
        Args:
            cleanup_interval: Interval between cleanup runs in seconds
        """
        if self._cleanup_task is None or self._cleanup_task.done():
            self._running = True
            self._cleanup_task = asyncio.create_task(self._cleanup_loop(cleanup_interval))
            logging.info(f"Started cache cleanup task with {cleanup_interval}s interval")
    
    async def _cleanup_loop(self, interval: float) -> None:
        """Background cleanup loop to remove expired entries."""
        while self._running:
            try:
                await asyncio.sleep(interval)
                self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Error in cache cleanup loop: {e}")
    
    def _cleanup_expired(self) -> None:
        """Remove expired entries from the cache."""
        with self._lock:
            expired_keys = []
            for key, entry in self._cache.items():
                if entry.is_expired():
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self._cache[key]
                self._stats['expired_cleanups'] += 1
            
            if expired_keys:
                logging.debug(f"Cleaned up {len(expired_keys)} expired cache entries")
            
            self._stats['cache_size'] = len(self._cache)
    
    def _evict_lru(self) -> None:
        """Evict least recently used items to make space."""
        with self._lock:
            while len(self._cache) >= self.max_size:
                # Remove the oldest item (LRU)
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
                self._stats['evictions'] += 1
            
            self._stats['cache_size'] = len(self._cache)
    
    def get(self, key: str) -> Optional[Any]:
        """Get a value from the cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found/expired
        """
        with self._lock:
            self._stats['total_requests'] += 1
            
            if key in self._cache:
                entry = self._cache[key]
                
                if entry.is_expired():
                    del self._cache[key]
                    self._stats['misses'] += 1
                    self._stats['cache_size'] = len(self._cache)
                    return None
                
                # Move to end (most recently used)
                self._cache.move_to_end(key)
                entry.update_access()
                
                self._stats['hits'] += 1
                self._update_hit_rate()
                return entry.value
            
            self._stats['misses'] += 1
            self._update_hit_rate()
            return None
    
    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Set a value in the cache.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds (uses default if None)
        """
        with self._lock:
            ttl = ttl or self.default_ttl
            
            # Evict old entries if necessary
            if len(self._cache) >= self.max_size:
                self._evict_lru()
            
            entry = CacheEntry(
                value=value,
                timestamp=time.time(),
                ttl=ttl
            )
            
            self._cache[key] = entry
            self._cache.move_to_end(key)  # Mark as most recently used
            
            self._stats['cache_size'] = len(self._cache)
    
    def delete(self, key: str) -> bool:
        """Delete a key from the cache.
        
        Args:
            key: Cache key to delete
            
        Returns:
            True if key was deleted, False if not found
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                self._stats['cache_size'] = len(self._cache)
                return True
            return False
    
    def clear(self) -> None:
        """Clear all entries from the cache."""
        with self._lock:
            self._cache.clear()
            self._stats['cache_size'] = 0
            logging.info("Cache cleared")
    
    def _update_hit_rate(self) -> None:
        """Update the hit rate statistic."""
        if self._stats['total_requests'] > 0:
            self._stats['hit_rate'] = (self._stats['hits'] / self._stats['total_requests']) * 100
    
    def get_stats(self) -> Dict[str, Union[int, float]]:
        """Get cache performance statistics."""
        with self._lock:
            return self._stats.copy()
    
    def get_memory_usage(self) -> Dict[str, Any]:
        """Get memory usage statistics."""
        with self._lock:
            total_entries = len(self._cache)
            avg_access_count = 0
            if total_entries > 0:
                avg_access_count = sum(entry.access_count for entry in self._cache.values()) / total_entries
            
            return {
                'total_entries': total_entries,
                'max_size': self.max_size,
                'usage_percentage': (total_entries / self.max_size) * 100 if self.max_size > 0 else 0,
                'avg_access_count': round(avg_access_count, 2)
            }
    
    async def stop_cleanup_task(self) -> None:
        """Stop the background cleanup task."""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            logging.info("Cache cleanup task stopped")


class SmartCache:
    """Smart caching system with automatic key generation and type-aware caching."""
    
    def __init__(self, cache: PerformanceCache):
        """Initialize smart cache.
        
        Args:
            cache: Underlying performance cache instance
        """
        self.cache = cache
    
    def _generate_key(self, func_name: str, args: tuple, kwargs: dict) -> str:
        """Generate a cache key from function name and arguments.
        
        Args:
            func_name: Function name
            args: Function arguments
            kwargs: Function keyword arguments
            
        Returns:
            Generated cache key
        """
        # Create a hashable representation of arguments
        key_data = {
            'func': func_name,
            'args': args,
            'kwargs': sorted(kwargs.items()) if kwargs else {}
        }
        
        # Convert to JSON string and hash for consistent keys
        key_str = json.dumps(key_data, sort_keys=True, default=str)
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def cached(self, ttl: Optional[float] = None, key_prefix: str = ""):
        """Decorator for caching function results.
        
        Args:
            ttl: Time-to-live for cached result
            key_prefix: Prefix for cache keys
        """
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                # Generate cache key
                cache_key = f"{key_prefix}{self._generate_key(func.__name__, args, kwargs)}"
                
                # Try to get from cache
                cached_result = self.cache.get(cache_key)
                if cached_result is not None:
                    logging.debug(f"Cache hit for {func.__name__}")
                    return cached_result
                
                # Execute function and cache result
                result = func(*args, **kwargs)
                self.cache.set(cache_key, result, ttl)
                logging.debug(f"Cached result for {func.__name__}")
                return result
            
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                # Generate cache key
                cache_key = f"{key_prefix}{self._generate_key(func.__name__, args, kwargs)}"
                
                # Try to get from cache
                cached_result = self.cache.get(cache_key)
                if cached_result is not None:
                    logging.debug(f"Cache hit for {func.__name__}")
                    return cached_result
                
                # Execute function and cache result
                result = await func(*args, **kwargs)
                self.cache.set(cache_key, result, ttl)
                logging.debug(f"Cached result for {func.__name__}")
                return result
            
            # Return appropriate wrapper based on function type
            if asyncio.iscoroutinefunction(func):
                return async_wrapper
            else:
                return sync_wrapper
        
        return decorator


# Global cache instances
_global_cache = PerformanceCache(max_size=2000, default_ttl=300.0)
_smart_cache = SmartCache(_global_cache)

# Convenience functions for global cache access
def get_global_cache() -> PerformanceCache:
    """Get the global cache instance."""
    return _global_cache

def get_smart_cache() -> SmartCache:
    """Get the global smart cache instance."""
    return _smart_cache

def cached(ttl: Optional[float] = None, key_prefix: str = ""):
    """Global cached decorator."""
    return _smart_cache.cached(ttl=ttl, key_prefix=key_prefix)

async def initialize_global_cache() -> None:
    """Initialize the global cache system."""
    _global_cache.start_cleanup_task()
    logging.info("Global cache system initialized")

async def cleanup_global_cache() -> None:
    """Cleanup the global cache system."""
    await _global_cache.stop_cleanup_task()
    logging.info("Global cache system cleaned up")