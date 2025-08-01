"""Main entry point for high-performance async LXC autoscaling."""

import asyncio
import logging
import signal
import sys
from typing import Dict, Any
import time

from async_scaling_orchestrator import AsyncScalingOrchestrator
from config_manager import config_manager
from lxc_utils import collect_container_data
from structured_logger import setup_structured_logging


class AsyncLXCAutoscaler:
    """High-performance async LXC autoscaler main application."""
    
    def __init__(self):
        """Initialize the async autoscaler."""
        self.orchestrator: AsyncScalingOrchestrator = None
        self.running = False
        self.shutdown_event = asyncio.Event()
        
    async def initialize(self) -> None:
        """Initialize the autoscaler and its components."""
        try:
            # Setup structured logging
            setup_structured_logging()
            
            # Initialize the async orchestrator
            self.orchestrator = AsyncScalingOrchestrator(
                max_concurrent_containers=config_manager.get_default('max_concurrent_containers', 20)
            )
            
            await self.orchestrator.initialize()
            
            # Validate system readiness
            is_ready = await self.orchestrator.validate_system_readiness_async()
            if not is_ready:
                raise RuntimeError("System readiness validation failed")
            
            logging.info("Async LXC autoscaler initialized successfully")
            
        except Exception as e:
            logging.error(f"Failed to initialize async autoscaler: {e}")
            raise
    
    async def collect_container_data_async(self) -> Dict[str, Dict[str, Any]]:
        """Collect container data asynchronously.
        
        Returns:
            Dictionary containing container resource usage data
        """
        try:
            # Run container data collection in executor to avoid blocking
            loop = asyncio.get_event_loop()
            container_data = await loop.run_in_executor(None, collect_container_data)
            
            logging.info(f"Collected data for {len(container_data)} containers")
            return container_data
            
        except Exception as e:
            logging.error(f"Error collecting container data: {e}")
            return {}
    
    async def run_scaling_cycle(self) -> Dict[str, Any]:
        """Run a single scaling cycle asynchronously.
        
        Returns:
            Dictionary containing cycle results
        """
        cycle_start = time.time()
        
        try:
            # Collect container data
            containers_data = await self.collect_container_data_async()
            
            if not containers_data:
                logging.warning("No container data collected, skipping scaling cycle")
                return {'success': False, 'reason': 'No container data'}
            
            # Determine if energy mode should be enabled
            energy_mode = (
                config_manager.get_default('energy_mode', False) and
                self.orchestrator.metrics_calculator.is_off_peak()
            )
            
            # Process the scaling cycle
            results = await self.orchestrator.process_scaling_cycle_async(
                containers_data, energy_mode
            )
            
            cycle_duration = time.time() - cycle_start
            logging.info(f"Scaling cycle completed in {cycle_duration:.2f}s")
            
            return results
            
        except Exception as e:
            cycle_duration = time.time() - cycle_start
            logging.error(f"Error in scaling cycle (duration: {cycle_duration:.2f}s): {e}")
            return {'success': False, 'error': str(e), 'duration': cycle_duration}
    
    async def run_continuous(self) -> None:
        """Run the autoscaler continuously with configurable intervals."""
        poll_interval = config_manager.get_default('poll_interval', 300)  # 5 minutes default
        
        logging.info(f"Starting continuous autoscaling with {poll_interval}s intervals")
        
        self.running = True
        cycle_count = 0
        
        try:
            while self.running and not self.shutdown_event.is_set():
                cycle_start = time.time()
                cycle_count += 1
                
                logging.info(f"Starting scaling cycle #{cycle_count}")
                
                # Run scaling cycle
                cycle_results = await self.run_scaling_cycle()
                
                # Log cycle summary
                if cycle_results.get('success', False):
                    logging.info(f"Cycle #{cycle_count} completed successfully")
                else:
                    logging.error(f"Cycle #{cycle_count} failed: {cycle_results.get('error', 'Unknown error')}")
                
                # Log performance statistics periodically
                if cycle_count % 10 == 0:  # Every 10 cycles
                    await self._log_performance_statistics()
                
                # Calculate time until next cycle
                cycle_duration = time.time() - cycle_start
                sleep_time = max(0, poll_interval - cycle_duration)
                
                if sleep_time > 0:
                    logging.info(f"Waiting {sleep_time:.1f}s until next cycle")
                    try:
                        await asyncio.wait_for(
                            self.shutdown_event.wait(),
                            timeout=sleep_time
                        )
                        break  # Shutdown requested
                    except asyncio.TimeoutError:
                        pass  # Normal timeout, continue to next cycle
                else:
                    logging.warning(f"Cycle took longer than poll interval ({cycle_duration:.1f}s > {poll_interval}s)")
                    
        except asyncio.CancelledError:
            logging.info("Continuous autoscaling cancelled")
        except Exception as e:
            logging.error(f"Error in continuous autoscaling: {e}")
        finally:
            self.running = False
            logging.info(f"Continuous autoscaling stopped after {cycle_count} cycles")
    
    async def _log_performance_statistics(self) -> None:
        """Log performance statistics for monitoring."""
        try:
            stats = self.orchestrator.get_performance_statistics()
            logging.info(f"Performance statistics: {stats}")
            
        except Exception as e:
            logging.error(f"Error logging performance statistics: {e}")
    
    async def shutdown(self) -> None:
        """Gracefully shutdown the autoscaler."""
        logging.info("Shutting down async autoscaler...")
        
        self.running = False
        self.shutdown_event.set()
        
        if self.orchestrator:
            await self.orchestrator.cleanup()
        
        logging.info("Async autoscaler shutdown completed")
    
    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            logging.info(f"Received signal {signum}, initiating shutdown...")
            asyncio.create_task(self.shutdown())
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)


async def main() -> int:
    """Main entry point for the async autoscaler."""
    autoscaler = AsyncLXCAutoscaler()
    
    try:
        # Initialize the autoscaler
        await autoscaler.initialize()
        
        # Setup signal handlers
        autoscaler._setup_signal_handlers()
        
        # Check if running in single-cycle mode
        if len(sys.argv) > 1 and sys.argv[1] == '--single-cycle':
            logging.info("Running in single-cycle mode")
            results = await autoscaler.run_scaling_cycle()
            
            if results.get('success', False):
                logging.info("Single cycle completed successfully")
                return 0
            else:
                logging.error(f"Single cycle failed: {results.get('error', 'Unknown error')}")
                return 1
        else:
            # Run continuously
            await autoscaler.run_continuous()
            return 0
            
    except KeyboardInterrupt:
        logging.info("Received keyboard interrupt")
        return 0
    except Exception as e:
        logging.error(f"Fatal error in main: {e}")
        return 1
    finally:
        await autoscaler.shutdown()


def run_async_autoscaler():
    """Entry point wrapper for the async autoscaler."""
    try:
        # Run the async main function
        return asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Autoscaler interrupted by user")
        return 0
    except Exception as e:
        logging.error(f"Failed to run async autoscaler: {e}")
        return 1


if __name__ == "__main__":
    exit_code = run_async_autoscaler()
    sys.exit(exit_code)