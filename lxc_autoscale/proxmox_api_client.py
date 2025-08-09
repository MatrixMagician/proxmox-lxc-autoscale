"""Proxmox API client for LXC container management.

This module provides a comprehensive API client for interacting with Proxmox VE
for LXC container management, replacing direct command execution with proper
API calls.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Union
from datetime import datetime, timedelta
import ssl

try:
    from proxmoxer import ProxmoxAPI
    from proxmoxer.core import ProxmoxHTTPAuth
except ImportError:
    logging.error("proxmoxer package not installed. Install with: pip install proxmoxer")
    ProxmoxAPI = None

import aiohttp
import json

from config_manager import config_manager
from error_handler import ErrorHandler


class ProxmoxAPIError(Exception):
    """Base exception for Proxmox API errors."""
    pass


class ProxmoxConnectionError(ProxmoxAPIError):
    """Exception raised for connection-related errors."""
    pass


class ProxmoxAuthenticationError(ProxmoxAPIError):
    """Exception raised for authentication errors."""
    pass


class ProxmoxAPIClient:
    """Synchronous Proxmox API client for LXC management."""
    
    def __init__(self, 
                 host: Optional[str] = None,
                 user: Optional[str] = None,
                 password: Optional[str] = None,
                 token_name: Optional[str] = None,
                 token_value: Optional[str] = None,
                 port: int = 8006,
                 verify_ssl: bool = True,
                 timeout: int = 30):
        """Initialize Proxmox API client.
        
        Args:
            host: Proxmox host address
            user: Username for authentication
            password: Password for authentication
            token_name: API token name (preferred over password)
            token_value: API token value
            port: Proxmox API port (default 8006)
            verify_ssl: Whether to verify SSL certificates
            timeout: Request timeout in seconds
        """
        if ProxmoxAPI is None:
            raise ImportError("proxmoxer package is required. Install with: pip install proxmoxer")
        
        # Get configuration values with fallbacks
        self.host = host or config_manager.get_default('proxmox_api_host', config_manager.get_default('proxmox_host'))
        self.user = user or config_manager.get_default('proxmox_api_user', 'root@pam')
        self.password = password or config_manager.get_default('proxmox_api_password')
        self.token_name = token_name or config_manager.get_default('proxmox_api_token_name')
        self.token_value = token_value or config_manager.get_default('proxmox_api_token_value')
        self.port = port
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        
        # Default node name
        self.node = config_manager.get_default('proxmox_node', config_manager.get_proxmox_hostname())
        
        self._client: Optional[ProxmoxAPI] = None
        self._authenticated = False
        self._last_auth_time: Optional[datetime] = None
        self._auth_ttl = timedelta(hours=1)  # Re-authenticate every hour
        
        logging.info(f"Initializing Proxmox API client for host: {self.host}")
    
    def _needs_reauthentication(self) -> bool:
        """Check if client needs re-authentication."""
        if not self._authenticated or self._last_auth_time is None:
            return True
        
        return datetime.now() - self._last_auth_time > self._auth_ttl
    
    def _authenticate(self) -> None:
        """Authenticate with Proxmox API."""
        if not self.host:
            raise ProxmoxConnectionError("Proxmox host not configured")
        
        try:
            # Prefer API token authentication over password
            if self.token_name and self.token_value:
                logging.debug("Using API token authentication")
                self._client = ProxmoxAPI(
                    self.host,
                    user=self.user,
                    token_name=self.token_name,
                    token_value=self.token_value,
                    port=self.port,
                    verify_ssl=self.verify_ssl,
                    timeout=self.timeout
                )
            elif self.password:
                logging.debug("Using password authentication")
                self._client = ProxmoxAPI(
                    self.host,
                    user=self.user,
                    password=self.password,
                    port=self.port,
                    verify_ssl=self.verify_ssl,
                    timeout=self.timeout
                )
            else:
                raise ProxmoxAuthenticationError("No authentication method configured (password or API token)")
            
            # Test the connection
            version = self._client.version.get()
            logging.info(f"Connected to Proxmox VE {version.get('version', 'unknown')}")
            
            self._authenticated = True
            self._last_auth_time = datetime.now()
            
        except Exception as e:
            self._authenticated = False
            self._client = None
            raise ProxmoxAuthenticationError(f"Failed to authenticate with Proxmox API: {e}")
    
    def _ensure_authenticated(self) -> ProxmoxAPI:
        """Ensure client is authenticated and return client instance."""
        if self._needs_reauthentication():
            self._authenticate()
        
        if not self._client:
            raise ProxmoxConnectionError("No authenticated Proxmox API client")
        
        return self._client
    
    def get_containers(self) -> List[Dict[str, Any]]:
        """Get list of LXC containers.
        
        Returns:
            List of container information dictionaries
        """
        try:
            client = self._ensure_authenticated()
            containers = client.nodes(self.node).lxc.get()
            
            logging.debug(f"Retrieved {len(containers)} containers from Proxmox API")
            return containers
            
        except Exception as e:
            logging.error(f"Failed to get containers: {e}")
            raise ProxmoxAPIError(f"Failed to get containers: {e}")
    
    def get_container_ids(self) -> List[str]:
        """Get list of container IDs.
        
        Returns:
            List of container ID strings
        """
        containers = self.get_containers()
        return [str(container['vmid']) for container in containers]
    
    def get_container_status(self, vmid: Union[int, str]) -> Dict[str, Any]:
        """Get container status information.
        
        Args:
            vmid: Container ID
            
        Returns:
            Container status dictionary
        """
        try:
            client = self._ensure_authenticated()
            status = client.nodes(self.node).lxc(vmid).status.current.get()
            
            logging.debug(f"Container {vmid} status: {status.get('status', 'unknown')}")
            return status
            
        except Exception as e:
            logging.error(f"Failed to get status for container {vmid}: {e}")
            raise ProxmoxAPIError(f"Failed to get container status: {e}")
    
    def is_container_running(self, vmid: Union[int, str]) -> bool:
        """Check if container is running.
        
        Args:
            vmid: Container ID
            
        Returns:
            True if container is running
        """
        try:
            status = self.get_container_status(vmid)
            return status.get('status') == 'running'
            
        except Exception:
            return False
    
    def get_container_config(self, vmid: Union[int, str]) -> Dict[str, Any]:
        """Get container configuration.
        
        Args:
            vmid: Container ID
            
        Returns:
            Container configuration dictionary
        """
        try:
            client = self._ensure_authenticated()
            config = client.nodes(self.node).lxc(vmid).config.get()
            
            logging.debug(f"Retrieved config for container {vmid}")
            return config
            
        except Exception as e:
            logging.error(f"Failed to get config for container {vmid}: {e}")
            raise ProxmoxAPIError(f"Failed to get container config: {e}")
    
    def update_container_config(self, vmid: Union[int, str], **config_params) -> bool:
        """Update container configuration.
        
        Args:
            vmid: Container ID
            **config_params: Configuration parameters to update
            
        Returns:
            True if update was successful
        """
        try:
            client = self._ensure_authenticated()
            result = client.nodes(self.node).lxc(vmid).config.post(**config_params)
            
            logging.info(f"Updated config for container {vmid}: {config_params}")
            return True
            
        except Exception as e:
            logging.error(f"Failed to update config for container {vmid}: {e}")
            raise ProxmoxAPIError(f"Failed to update container config: {e}")
    
    def resize_container(self, vmid: Union[int, str], disk: str, size: str) -> bool:
        """Resize container disk.
        
        Args:
            vmid: Container ID
            disk: Disk identifier (e.g., 'rootfs')
            size: New size (e.g., '+2G', '10G')
            
        Returns:
            True if resize was successful
        """
        try:
            client = self._ensure_authenticated()
            result = client.nodes(self.node).lxc(vmid).resize.put(disk=disk, size=size)
            
            logging.info(f"Resized disk {disk} for container {vmid} to {size}")
            return True
            
        except Exception as e:
            logging.error(f"Failed to resize container {vmid}: {e}")
            raise ProxmoxAPIError(f"Failed to resize container: {e}")
    
    def get_container_rrd_data(self, vmid: Union[int, str], timeframe: str = 'hour') -> Dict[str, Any]:
        """Get container resource usage data (RRD).
        
        Args:
            vmid: Container ID
            timeframe: Time frame for data ('hour', 'day', 'week', 'month', 'year')
            
        Returns:
            RRD data dictionary
        """
        try:
            client = self._ensure_authenticated()
            rrd_data = client.nodes(self.node).lxc(vmid).rrd.get(timeframe=timeframe)
            
            logging.debug(f"Retrieved RRD data for container {vmid} (timeframe: {timeframe})")
            return rrd_data
            
        except Exception as e:
            logging.error(f"Failed to get RRD data for container {vmid}: {e}")
            raise ProxmoxAPIError(f"Failed to get container RRD data: {e}")
    
    def clone_container(self, vmid: Union[int, str], newid: Union[int, str], 
                       hostname: Optional[str] = None, **clone_params) -> bool:
        """Clone a container.
        
        Args:
            vmid: Source container ID
            newid: New container ID
            hostname: Hostname for the new container
            **clone_params: Additional clone parameters
            
        Returns:
            True if clone was successful
        """
        try:
            client = self._ensure_authenticated()
            
            params = {'newid': newid}
            if hostname:
                params['hostname'] = hostname
            params.update(clone_params)
            
            result = client.nodes(self.node).lxc(vmid).clone.post(**params)
            
            logging.info(f"Cloned container {vmid} to {newid}")
            return True
            
        except Exception as e:
            logging.error(f"Failed to clone container {vmid} to {newid}: {e}")
            raise ProxmoxAPIError(f"Failed to clone container: {e}")
    
    def start_container(self, vmid: Union[int, str]) -> bool:
        """Start a container.
        
        Args:
            vmid: Container ID
            
        Returns:
            True if start was successful
        """
        try:
            client = self._ensure_authenticated()
            result = client.nodes(self.node).lxc(vmid).status.start.post()
            
            logging.info(f"Started container {vmid}")
            return True
            
        except Exception as e:
            logging.error(f"Failed to start container {vmid}: {e}")
            raise ProxmoxAPIError(f"Failed to start container: {e}")
    
    def stop_container(self, vmid: Union[int, str]) -> bool:
        """Stop a container.
        
        Args:
            vmid: Container ID
            
        Returns:
            True if stop was successful
        """
        try:
            client = self._ensure_authenticated()
            result = client.nodes(self.node).lxc(vmid).status.stop.post()
            
            logging.info(f"Stopped container {vmid}")
            return True
            
        except Exception as e:
            logging.error(f"Failed to stop container {vmid}: {e}")
            raise ProxmoxAPIError(f"Failed to stop container: {e}")
    
    def get_node_status(self) -> Dict[str, Any]:
        """Get node status information.
        
        Returns:
            Node status dictionary
        """
        try:
            client = self._ensure_authenticated()
            status = client.nodes(self.node).status.get()
            
            logging.debug(f"Retrieved node status for {self.node}")
            return status
            
        except Exception as e:
            logging.error(f"Failed to get node status: {e}")
            raise ProxmoxAPIError(f"Failed to get node status: {e}")


class AsyncProxmoxAPIClient:
    """Asynchronous Proxmox API client for LXC management."""
    
    def __init__(self, 
                 host: Optional[str] = None,
                 user: Optional[str] = None,
                 password: Optional[str] = None,
                 token_name: Optional[str] = None,
                 token_value: Optional[str] = None,
                 port: int = 8006,
                 verify_ssl: bool = True,
                 timeout: int = 30):
        """Initialize async Proxmox API client.
        
        Args:
            host: Proxmox host address
            user: Username for authentication
            password: Password for authentication
            token_name: API token name (preferred over password)
            token_value: API token value
            port: Proxmox API port (default 8006)
            verify_ssl: Whether to verify SSL certificates
            timeout: Request timeout in seconds
        """
        # Get configuration values with fallbacks
        self.host = host or config_manager.get_default('proxmox_api_host', config_manager.get_default('proxmox_host'))
        self.user = user or config_manager.get_default('proxmox_api_user', 'root@pam')
        self.password = password or config_manager.get_default('proxmox_api_password')
        self.token_name = token_name or config_manager.get_default('proxmox_api_token_name')
        self.token_value = token_value or config_manager.get_default('proxmox_api_token_value')
        self.port = port
        self.verify_ssl = verify_ssl
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        
        # Default node name
        self.node = config_manager.get_default('proxmox_node', config_manager.get_proxmox_hostname())
        
        self._session: Optional[aiohttp.ClientSession] = None
        self._auth_token: Optional[str] = None
        self._auth_expires: Optional[datetime] = None
        
        # Build base URL
        protocol = 'https' if self.verify_ssl else 'http'
        self.base_url = f"{protocol}://{self.host}:{self.port}/api2/json"
        
        logging.info(f"Initializing async Proxmox API client for host: {self.host}")
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                ssl=ssl.create_default_context() if self.verify_ssl else False,
                limit=100,
                limit_per_host=30
            )
            
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=self.timeout,
                headers={'Content-Type': 'application/json'}
            )
        
        return self._session
    
    async def _authenticate(self) -> None:
        """Authenticate with Proxmox API."""
        if not self.host:
            raise ProxmoxConnectionError("Proxmox host not configured")
        
        session = await self._get_session()
        
        try:
            # Prefer API token authentication
            if self.token_name and self.token_value:
                logging.debug("Using API token authentication for async client")
                self._auth_token = f"PVEAPIToken={self.user}!{self.token_name}={self.token_value}"
                self._auth_expires = datetime.now() + timedelta(hours=24)  # API tokens don't expire
                
            elif self.password:
                logging.debug("Using password authentication for async client")
                
                auth_data = {
                    'username': self.user,
                    'password': self.password
                }
                
                async with session.post(f"{self.base_url}/access/ticket", data=auth_data) as response:
                    if response.status != 200:
                        raise ProxmoxAuthenticationError(f"Authentication failed: {response.status}")
                    
                    result = await response.json()
                    data = result.get('data', {})
                    
                    ticket = data.get('ticket')
                    csrf_token = data.get('CSRFPreventionToken')
                    
                    if not ticket:
                        raise ProxmoxAuthenticationError("No ticket received from authentication")
                    
                    self._auth_token = f"PVEAuthCookie={ticket}"
                    if csrf_token:
                        session.headers['CSRFPreventionToken'] = csrf_token
                    
                    # Set expiration time (typically 2 hours for tickets)
                    self._auth_expires = datetime.now() + timedelta(hours=1, minutes=45)
                    
            else:
                raise ProxmoxAuthenticationError("No authentication method configured")
            
            # Set authorization header
            session.headers['Authorization'] = self._auth_token
            
            logging.info("Successfully authenticated with Proxmox API (async)")
            
        except aiohttp.ClientError as e:
            raise ProxmoxConnectionError(f"Network error during authentication: {e}")
        except Exception as e:
            raise ProxmoxAuthenticationError(f"Failed to authenticate: {e}")
    
    async def _ensure_authenticated(self) -> aiohttp.ClientSession:
        """Ensure client is authenticated and return session."""
        if (self._auth_token is None or 
            self._auth_expires is None or 
            datetime.now() >= self._auth_expires):
            await self._authenticate()
        
        return await self._get_session()
    
    async def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Make authenticated API request.
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            **kwargs: Additional request parameters
            
        Returns:
            Response data dictionary
        """
        session = await self._ensure_authenticated()
        url = f"{self.base_url}{endpoint}"
        
        try:
            async with session.request(method, url, **kwargs) as response:
                if response.status == 401:
                    # Token might have expired, retry with fresh authentication
                    self._auth_token = None
                    session = await self._ensure_authenticated()
                    async with session.request(method, url, **kwargs) as retry_response:
                        if retry_response.status >= 400:
                            raise ProxmoxAPIError(f"API request failed: {retry_response.status}")
                        return await retry_response.json()
                
                elif response.status >= 400:
                    raise ProxmoxAPIError(f"API request failed: {response.status}")
                
                return await response.json()
                
        except aiohttp.ClientError as e:
            raise ProxmoxConnectionError(f"Network error: {e}")
    
    async def get_containers(self) -> List[Dict[str, Any]]:
        """Get list of LXC containers asynchronously.
        
        Returns:
            List of container information dictionaries
        """
        try:
            result = await self._make_request('GET', f'/nodes/{self.node}/lxc')
            containers = result.get('data', [])
            
            logging.debug(f"Retrieved {len(containers)} containers from Proxmox API (async)")
            return containers
            
        except Exception as e:
            logging.error(f"Failed to get containers (async): {e}")
            raise ProxmoxAPIError(f"Failed to get containers: {e}")
    
    async def get_container_ids(self) -> List[str]:
        """Get list of container IDs asynchronously.
        
        Returns:
            List of container ID strings
        """
        containers = await self.get_containers()
        return [str(container['vmid']) for container in containers]
    
    async def get_container_status(self, vmid: Union[int, str]) -> Dict[str, Any]:
        """Get container status information asynchronously.
        
        Args:
            vmid: Container ID
            
        Returns:
            Container status dictionary
        """
        try:
            result = await self._make_request('GET', f'/nodes/{self.node}/lxc/{vmid}/status/current')
            status = result.get('data', {})
            
            logging.debug(f"Container {vmid} status: {status.get('status', 'unknown')} (async)")
            return status
            
        except Exception as e:
            logging.error(f"Failed to get status for container {vmid} (async): {e}")
            raise ProxmoxAPIError(f"Failed to get container status: {e}")
    
    async def is_container_running(self, vmid: Union[int, str]) -> bool:
        """Check if container is running asynchronously.
        
        Args:
            vmid: Container ID
            
        Returns:
            True if container is running
        """
        try:
            status = await self.get_container_status(vmid)
            return status.get('status') == 'running'
            
        except Exception:
            return False
    
    async def get_container_config(self, vmid: Union[int, str]) -> Dict[str, Any]:
        """Get container configuration asynchronously.
        
        Args:
            vmid: Container ID
            
        Returns:
            Container configuration dictionary
        """
        try:
            result = await self._make_request('GET', f'/nodes/{self.node}/lxc/{vmid}/config')
            config = result.get('data', {})
            
            logging.debug(f"Retrieved config for container {vmid} (async)")
            return config
            
        except Exception as e:
            logging.error(f"Failed to get config for container {vmid} (async): {e}")
            raise ProxmoxAPIError(f"Failed to get container config: {e}")
    
    async def update_container_config(self, vmid: Union[int, str], **config_params) -> bool:
        """Update container configuration asynchronously.
        
        Args:
            vmid: Container ID
            **config_params: Configuration parameters to update
            
        Returns:
            True if update was successful
        """
        try:
            await self._make_request('POST', f'/nodes/{self.node}/lxc/{vmid}/config', data=config_params)
            
            logging.info(f"Updated config for container {vmid}: {config_params} (async)")
            return True
            
        except Exception as e:
            logging.error(f"Failed to update config for container {vmid} (async): {e}")
            raise ProxmoxAPIError(f"Failed to update container config: {e}")
    
    async def get_container_rrd_data(self, vmid: Union[int, str], timeframe: str = 'hour') -> List[Dict[str, Any]]:
        """Get container resource usage data (RRD) asynchronously.
        
        Args:
            vmid: Container ID
            timeframe: Time frame for data ('hour', 'day', 'week', 'month', 'year')
            
        Returns:
            List of RRD data points
        """
        try:
            result = await self._make_request('GET', f'/nodes/{self.node}/lxc/{vmid}/rrd', 
                                            params={'timeframe': timeframe})
            rrd_data = result.get('data', [])
            
            logging.debug(f"Retrieved RRD data for container {vmid} (timeframe: {timeframe}) (async)")
            return rrd_data
            
        except Exception as e:
            logging.error(f"Failed to get RRD data for container {vmid} (async): {e}")
            raise ProxmoxAPIError(f"Failed to get container RRD data: {e}")
    
    async def clone_container(self, vmid: Union[int, str], newid: Union[int, str], 
                             hostname: Optional[str] = None, **clone_params) -> bool:
        """Clone a container asynchronously.
        
        Args:
            vmid: Source container ID
            newid: New container ID
            hostname: Hostname for the new container
            **clone_params: Additional clone parameters
            
        Returns:
            True if clone was successful
        """
        try:
            params = {'newid': newid}
            if hostname:
                params['hostname'] = hostname
            params.update(clone_params)
            
            await self._make_request('POST', f'/nodes/{self.node}/lxc/{vmid}/clone', data=params)
            
            logging.info(f"Cloned container {vmid} to {newid} (async)")
            return True
            
        except Exception as e:
            logging.error(f"Failed to clone container {vmid} to {newid} (async): {e}")
            raise ProxmoxAPIError(f"Failed to clone container: {e}")
    
    async def start_container(self, vmid: Union[int, str]) -> bool:
        """Start a container asynchronously.
        
        Args:
            vmid: Container ID
            
        Returns:
            True if start was successful
        """
        try:
            await self._make_request('POST', f'/nodes/{self.node}/lxc/{vmid}/status/start')
            
            logging.info(f"Started container {vmid} (async)")
            return True
            
        except Exception as e:
            logging.error(f"Failed to start container {vmid} (async): {e}")
            raise ProxmoxAPIError(f"Failed to start container: {e}")
    
    async def stop_container(self, vmid: Union[int, str]) -> bool:
        """Stop a container asynchronously.
        
        Args:
            vmid: Container ID
            
        Returns:
            True if stop was successful
        """
        try:
            await self._make_request('POST', f'/nodes/{self.node}/lxc/{vmid}/status/stop')
            
            logging.info(f"Stopped container {vmid} (async)")
            return True
            
        except Exception as e:
            logging.error(f"Failed to stop container {vmid} (async): {e}")
            raise ProxmoxAPIError(f"Failed to stop container: {e}")
    
    async def close(self) -> None:
        """Close the async session."""
        if self._session and not self._session.closed:
            await self._session.close()
            logging.debug("Closed async Proxmox API client session")


# Global instances
_sync_client: Optional[ProxmoxAPIClient] = None
_async_client: Optional[AsyncProxmoxAPIClient] = None


def get_proxmox_client() -> ProxmoxAPIClient:
    """Get global synchronous Proxmox API client instance."""
    global _sync_client
    if _sync_client is None:
        _sync_client = ProxmoxAPIClient()
    return _sync_client


def get_async_proxmox_client() -> AsyncProxmoxAPIClient:
    """Get global asynchronous Proxmox API client instance."""
    global _async_client
    if _async_client is None:
        _async_client = AsyncProxmoxAPIClient()
    return _async_client


async def close_async_client() -> None:
    """Close the global async client."""
    global _async_client
    if _async_client:
        await _async_client.close()
        _async_client = None