"""Centralized command execution for local and remote operations."""

import logging
import subprocess
from typing import Optional

import paramiko

from constants import DEFAULT_COMMAND_TIMEOUT, DEFAULT_SSH_TIMEOUT
from error_handler import SSHConnectionError, handle_ssh_errors, retry_on_failure


class CommandExecutor:
    """Handles command execution both locally and remotely via SSH."""
    
    def __init__(self, config_manager):
        """Initialize the command executor.
        
        Args:
            config_manager: Configuration manager instance
        """
        self.config_manager = config_manager
        self._ssh_client: Optional[paramiko.SSHClient] = None
    
    @property
    def use_remote(self) -> bool:
        """Check if remote execution should be used."""
        return self.config_manager.get_default('use_remote_proxmox', False)
    
    @handle_ssh_errors
    @retry_on_failure(max_retries=2, exceptions=(paramiko.SSHException, ConnectionError))
    def _get_ssh_client(self) -> Optional[paramiko.SSHClient]:
        """Get or create SSH client with connection pooling."""
        if self._ssh_client is None:
            logging.debug("Creating new SSH connection...")
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            try:
                ssh.connect(
                    hostname=self.config_manager.get_default('proxmox_host'),
                    port=self.config_manager.get_default('ssh_port', 22),
                    username=self.config_manager.get_default('ssh_user'),
                    password=self.config_manager.get_default('ssh_password'),
                    key_filename=self.config_manager.get_default('ssh_key_path'),
                    timeout=self.config_manager.get_default('ssh_timeout', DEFAULT_SSH_TIMEOUT)
                )
                logging.info("SSH connection established successfully")
                self._ssh_client = ssh
            except Exception as e:
                logging.error(f"Failed to create SSH client: {e}")
                raise SSHConnectionError(f"SSH connection failed: {e}") from e
        
        return self._ssh_client
    
    def close_ssh_connection(self) -> None:
        """Close the SSH client connection."""
        if self._ssh_client:
            logging.debug("Closing SSH connection...")
            self._ssh_client.close()
            self._ssh_client = None
            logging.info("SSH connection closed")
    
    @retry_on_failure(max_retries=2, exceptions=(subprocess.TimeoutExpired, subprocess.CalledProcessError))
    def _execute_local_command(self, cmd: str, timeout: int = DEFAULT_COMMAND_TIMEOUT) -> Optional[str]:
        """Execute a command locally with timeout and retry logic.
        
        Args:
            cmd: The command to execute
            timeout: Timeout in seconds for the command execution
            
        Returns:
            The command output or None if the command failed
        """
        try:
            result = subprocess.check_output(
                cmd,
                shell=True,
                timeout=timeout,
                stderr=subprocess.STDOUT,
                text=True
            ).strip()
            logging.debug(f"Local command '{cmd}' executed successfully")
            return result
        except subprocess.TimeoutExpired:
            logging.error(f"Local command '{cmd}' timed out after {timeout} seconds")
            raise
        except subprocess.CalledProcessError as e:
            logging.error(f"Local command '{cmd}' failed with exit code {e.returncode}: {e.output}")
            raise
        except Exception as e:
            logging.error(f"Unexpected error executing local command '{cmd}': {e}")
            return None
    
    @handle_ssh_errors
    @retry_on_failure(max_retries=2, exceptions=(paramiko.SSHException,))
    def _execute_remote_command(self, cmd: str, timeout: int = DEFAULT_COMMAND_TIMEOUT) -> Optional[str]:
        """Execute a command remotely via SSH.
        
        Args:
            cmd: The command to execute
            timeout: Timeout in seconds for the command execution
            
        Returns:
            The command output or None if the command failed
        """
        ssh_client = self._get_ssh_client()
        if not ssh_client:
            raise SSHConnectionError("No SSH client available")
        
        try:
            stdin, stdout, stderr = ssh_client.exec_command(cmd, timeout=timeout)
            
            # Wait for command completion
            exit_status = stdout.channel.recv_exit_status()
            
            if exit_status == 0:
                output = stdout.read().decode('utf-8').strip()
                logging.debug(f"Remote command '{cmd}' executed successfully")
                return output
            else:
                error_output = stderr.read().decode('utf-8').strip()
                logging.error(f"Remote command '{cmd}' failed with exit code {exit_status}: {error_output}")
                return None
                
        except Exception as e:
            logging.error(f"Error executing remote command '{cmd}': {e}")
            # Reset SSH client on error to force reconnection
            self.close_ssh_connection()
            raise
    
    def execute(self, cmd: str, timeout: int = DEFAULT_COMMAND_TIMEOUT) -> Optional[str]:
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
        
        logging.debug(f"Executing command: {cmd} (timeout: {timeout}s, remote: {self.use_remote})")
        
        try:
            if self.use_remote:
                return self._execute_remote_command(cmd, timeout)
            else:
                return self._execute_local_command(cmd, timeout)
        except Exception as e:
            logging.error(f"Command execution failed: {e}")
            return None
    
    def execute_proxmox_command(self, cmd: str, timeout: int = DEFAULT_COMMAND_TIMEOUT) -> Optional[str]:
        """Execute a Proxmox-specific command with additional validation.
        
        Args:
            cmd: The Proxmox command to execute
            timeout: Timeout in seconds for the command execution
            
        Returns:
            The command output or None if the command failed
        """
        # Validate that this is a Proxmox command
        valid_proxmox_commands = ['pct', 'qm', 'pvesh', 'pvesm', 'pvecm']
        cmd_parts = cmd.split()
        
        if not cmd_parts or cmd_parts[0] not in valid_proxmox_commands:
            logging.error(f"Invalid Proxmox command: {cmd}")
            return None
        
        return self.execute(cmd, timeout)
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup connections."""
        self.close_ssh_connection()