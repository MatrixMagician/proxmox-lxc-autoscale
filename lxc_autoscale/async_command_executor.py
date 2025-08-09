"""Asynchronous command execution for high-performance operations using Proxmox API."""

import asyncio
import logging
import subprocess
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor
import time

from constants import DEFAULT_COMMAND_TIMEOUT
from error_handler import handle_configuration_errors


class AsyncCommandExecutor:
    """High-performance asynchronous command executor using Proxmox API exclusively."""
    
    def __init__(self, config_manager, max_concurrent_commands: int = 10):
        """Initialize the async command executor.
        
        Args:
            config_manager: Configuration manager instance
            max_concurrent_commands: Maximum number of concurrent commands
        """
        self.config_manager = config_manager
        self.max_concurrent_commands = max_concurrent_commands
        self._semaphore = asyncio.Semaphore(max_concurrent_commands)
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent_commands)
        
        # Performance metrics
        self._command_stats = {
            'total_commands': 0,
            'successful_commands': 0,
            'failed_commands': 0,
            'avg_execution_time': 0.0
        }
    
    async def execute_local_command(self, cmd: str, timeout: int = DEFAULT_COMMAND_TIMEOUT) -> Optional[str]:
        """Execute a command locally with async support.
        
        Args:
            cmd: The command to execute
            timeout: Timeout in seconds for the command execution
            
        Returns:
            The command output or None if the command failed
        """
        start_time = time.time()
        
        try:
            # Use asyncio subprocess for better performance
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
            
            if process.returncode == 0:
                result = stdout.decode('utf-8').strip()
                self._update_stats(True, time.time() - start_time)
                logging.debug(f"Local command '{cmd}' executed successfully")
                return result
            else:
                error_output = stderr.decode('utf-8').strip()
                logging.error(f"Local command '{cmd}' failed with exit code {process.returncode}: {error_output}")
                self._update_stats(False, time.time() - start_time)
                return None
                
        except asyncio.TimeoutError:
            logging.error(f"Local command '{cmd}' timed out after {timeout} seconds")
            self._update_stats(False, time.time() - start_time)
            return None
        except Exception as e:
            logging.error(f"Unexpected error executing local command '{cmd}': {e}")
            self._update_stats(False, time.time() - start_time)
            return None
    
    async def execute(self, cmd: str, timeout: int = DEFAULT_COMMAND_TIMEOUT) -> Optional[str]:
        """Execute a command locally using Proxmox API approach.
        
        Note: This function now only executes local commands since we're using
        Proxmox API exclusively. Remote execution is handled by the API client.
        
        Args:
            cmd: The command to execute
            timeout: Timeout in seconds for the command execution
            
        Returns:
            The command output or None if the command failed
        """
        # Validate and sanitize command input
        if not cmd or not isinstance(cmd, str):
            logging.error("Invalid command provided")
            return None
        
        # Basic command injection protection
        if any(char in cmd for char in [';', '&&', '||', '|', '`', '$(']):
            logging.warning(f"Potentially unsafe command detected: {cmd}")
        
        async with self._semaphore:
            logging.debug(f"Executing local command: {cmd} (timeout: {timeout}s)")
            return await self.execute_local_command(cmd, timeout)
    
    async def execute_batch(self, commands: List[Tuple[str, int]], max_concurrent: int = None) -> List[Optional[str]]:
        """Execute multiple commands concurrently for better performance.
        
        Args:
            commands: List of (command, timeout) tuples
            max_concurrent: Maximum concurrent executions (defaults to class limit)
            
        Returns:
            List of command outputs in the same order as input
        """
        if not commands:
            return []
        
        concurrent_limit = min(max_concurrent or self.max_concurrent_commands, len(commands))
        semaphore = asyncio.Semaphore(concurrent_limit)
        
        async def execute_with_semaphore(cmd_timeout):
            cmd, timeout = cmd_timeout
            async with semaphore:
                return await self.execute(cmd, timeout)
        
        # Execute all commands concurrently
        tasks = [execute_with_semaphore(cmd_timeout) for cmd_timeout in commands]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Convert exceptions to None
        return [result if not isinstance(result, Exception) else None for result in results]
    
    async def execute_proxmox_commands_batch(self, commands: List[Tuple[str, int]]) -> List[Optional[str]]:
        """Execute multiple Proxmox commands concurrently.
        
        Note: This is now for local Proxmox commands only, as remote operations
        are handled by the Proxmox API client.
        
        Args:
            commands: List of (command, timeout) tuples
            
        Returns:
            List of command outputs in the same order as input
        """
        # Validate all commands first
        valid_proxmox_commands = ['pct', 'qm', 'pvesh', 'pvesm', 'pvecm']
        validated_commands = []
        
        for cmd, timeout in commands:
            cmd_parts = cmd.split()
            if cmd_parts and cmd_parts[0] in valid_proxmox_commands:
                validated_commands.append((cmd, timeout))
            else:
                logging.error(f"Invalid Proxmox command: {cmd}")
                validated_commands.append(None)
        
        # Execute validated commands
        results = []
        valid_commands = [(cmd, timeout) for cmd, timeout in validated_commands if cmd is not None]
        
        if valid_commands:
            execution_results = await self.execute_batch(valid_commands)
            result_iter = iter(execution_results)
            
            for cmd_timeout in validated_commands:
                if cmd_timeout is None:
                    results.append(None)
                else:
                    results.append(next(result_iter))
        else:
            results = [None] * len(commands)
        
        return results
    
    def _update_stats(self, success: bool, execution_time: float) -> None:
        """Update performance statistics."""
        self._command_stats['total_commands'] += 1
        if success:
            self._command_stats['successful_commands'] += 1
        else:
            self._command_stats['failed_commands'] += 1
        
        # Update average execution time
        total = self._command_stats['total_commands']
        current_avg = self._command_stats['avg_execution_time']
        self._command_stats['avg_execution_time'] = ((current_avg * (total - 1)) + execution_time) / total
    
    def get_performance_stats(self) -> Dict[str, float]:
        """Get current performance statistics."""
        stats = self._command_stats.copy()
        if stats['total_commands'] > 0:
            stats['success_rate'] = (stats['successful_commands'] / stats['total_commands']) * 100
        else:
            stats['success_rate'] = 0.0
        return stats
    
    async def cleanup(self) -> None:
        """Cleanup resources used by the executor."""
        try:
            # Shutdown thread pool
            self._executor.shutdown(wait=True)
            
            logging.info("Async command executor cleanup completed")
            
        except Exception as e:
            logging.error(f"Error during async executor cleanup: {e}")
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.cleanup()