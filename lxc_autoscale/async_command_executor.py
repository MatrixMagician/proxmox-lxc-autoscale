"""Asynchronous command execution for high-performance operations."""

import asyncio
import logging
import subprocess
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import time

import paramiko
import asyncssh

from constants import DEFAULT_COMMAND_TIMEOUT, DEFAULT_SSH_TIMEOUT
from error_handler import SSHConnectionError, handle_ssh_errors, retry_on_failure


class AsyncCommandExecutor:
    """High-performance asynchronous command executor with connection pooling."""
    
    def __init__(self, config_manager, max_concurrent_commands: int = 10):
        """Initialize the async command executor.
        
        Args:
            config_manager: Configuration manager instance
            max_concurrent_commands: Maximum number of concurrent commands
        """
        self.config_manager = config_manager
        self.max_concurrent_commands = max_concurrent_commands
        self._semaphore = asyncio.Semaphore(max_concurrent_commands)
        self._ssh_pool: List[asyncssh.SSHClientConnection] = []
        self._pool_lock = asyncio.Lock()
        self._connection_cache: Dict[str, asyncssh.SSHClientConnection] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent_commands)
        
        # Performance metrics
        self._command_stats = {
            'total_commands': 0,
            'successful_commands': 0,
            'failed_commands': 0,
            'avg_execution_time': 0.0,
            'cache_hits': 0
        }
    
    @property
    def use_remote(self) -> bool:
        """Check if remote execution should be used."""
        return self.config_manager.get_default('use_remote_proxmox', False)
    
    async def initialize_pool(self, pool_size: int = 5) -> None:
        """Initialize SSH connection pool for better performance.
        
        Args:
            pool_size: Size of the connection pool
        """
        if not self.use_remote:
            return
        
        async with self._pool_lock:
            try:
                host = self.config_manager.get_default('proxmox_host')
                port = self.config_manager.get_default('ssh_port', 22)
                username = self.config_manager.get_default('ssh_user')
                password = self.config_manager.get_default('ssh_password')
                key_filename = self.config_manager.get_default('ssh_key_path')
                
                # Create connection pool
                for _ in range(pool_size):
                    try:
                        conn = await asyncssh.connect(
                            host=host,
                            port=port,
                            username=username,
                            password=password,
                            client_keys=[key_filename] if key_filename else None,
                            known_hosts=None,
                            server_host_key_algs=['ssh-rsa', 'rsa-sha2-256', 'rsa-sha2-512'],
                            connect_timeout=self.config_manager.get_default('ssh_timeout', DEFAULT_SSH_TIMEOUT),
                            keepalive_interval=30
                        )
                        self._ssh_pool.append(conn)
                        logging.debug(f"Created SSH connection {len(self._ssh_pool)}/{pool_size}")
                    except Exception as e:
                        logging.warning(f"Failed to create SSH connection: {e}")
                
                logging.info(f"SSH pool initialized with {len(self._ssh_pool)} connections")
                
            except Exception as e:
                logging.error(f"Failed to initialize SSH pool: {e}")
                raise SSHConnectionError(f"SSH pool initialization failed: {e}") from e
    
    async def _get_ssh_connection(self) -> Optional[asyncssh.SSHClientConnection]:
        """Get an SSH connection from the pool."""
        async with self._pool_lock:
            if self._ssh_pool:
                conn = self._ssh_pool.pop()
                # Test if connection is still alive
                try:
                    await conn.run('echo test', timeout=5)
                    return conn
                except Exception:
                    # Connection is dead, create a new one
                    try:
                        await conn.close()
                    except:
                        pass
            
            # Create new connection if pool is empty or connection failed
            try:
                host = self.config_manager.get_default('proxmox_host')
                port = self.config_manager.get_default('ssh_port', 22)
                username = self.config_manager.get_default('ssh_user')
                password = self.config_manager.get_default('ssh_password')
                key_filename = self.config_manager.get_default('ssh_key_path')
                
                conn = await asyncssh.connect(
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    client_keys=[key_filename] if key_filename else None,
                    known_hosts=None,
                    server_host_key_algs=['ssh-rsa', 'rsa-sha2-256', 'rsa-sha2-512'],
                    connect_timeout=self.config_manager.get_default('ssh_timeout', DEFAULT_SSH_TIMEOUT),
                    keepalive_interval=30
                )
                return conn
            except Exception as e:
                logging.error(f"Failed to create SSH connection: {e}")
                return None
    
    async def _return_ssh_connection(self, conn: asyncssh.SSHClientConnection) -> None:
        """Return an SSH connection to the pool."""
        async with self._pool_lock:
            if len(self._ssh_pool) < self.max_concurrent_commands:
                self._ssh_pool.append(conn)
            else:
                try:
                    await conn.close()
                except:
                    pass
    
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
    
    async def execute_remote_command(self, cmd: str, timeout: int = DEFAULT_COMMAND_TIMEOUT) -> Optional[str]:
        """Execute a command remotely via SSH with connection pooling.
        
        Args:
            cmd: The command to execute
            timeout: Timeout in seconds for the command execution
            
        Returns:
            The command output or None if the command failed
        """
        start_time = time.time()
        conn = None
        
        try:
            conn = await self._get_ssh_connection()
            if not conn:
                self._update_stats(False, time.time() - start_time)
                return None
            
            result = await conn.run(cmd, timeout=timeout)
            
            if result.exit_status == 0:
                output = result.stdout.strip()
                self._update_stats(True, time.time() - start_time)
                logging.debug(f"Remote command '{cmd}' executed successfully")
                await self._return_ssh_connection(conn)
                return output
            else:
                error_output = result.stderr.strip()
                logging.error(f"Remote command '{cmd}' failed with exit code {result.exit_status}: {error_output}")
                self._update_stats(False, time.time() - start_time)
                await self._return_ssh_connection(conn)
                return None
                
        except asyncio.TimeoutError:
            logging.error(f"Remote command '{cmd}' timed out after {timeout} seconds")
            self._update_stats(False, time.time() - start_time)
            if conn:
                try:
                    await conn.close()
                except:
                    pass
            return None
        except Exception as e:
            logging.error(f"Error executing remote command '{cmd}': {e}")
            self._update_stats(False, time.time() - start_time)
            if conn:
                try:
                    await conn.close()
                except:
                    pass
            return None
    
    async def execute(self, cmd: str, timeout: int = DEFAULT_COMMAND_TIMEOUT) -> Optional[str]:
        """Execute a command locally or remotely based on configuration.
        
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
            logging.debug(f"Executing command: {cmd} (timeout: {timeout}s, remote: {self.use_remote})")
            
            if self.use_remote:
                return await self.execute_remote_command(cmd, timeout)
            else:
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
            # Close all SSH connections
            async with self._pool_lock:
                for conn in self._ssh_pool:
                    try:
                        await conn.close()
                    except:
                        pass
                self._ssh_pool.clear()
            
            # Close cached connections
            for conn in self._connection_cache.values():
                try:
                    await conn.close()
                except:
                    pass
            self._connection_cache.clear()
            
            # Shutdown thread pool
            self._executor.shutdown(wait=True)
            
            logging.info("Async command executor cleanup completed")
            
        except Exception as e:
            logging.error(f"Error during async executor cleanup: {e}")
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.initialize_pool()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.cleanup()