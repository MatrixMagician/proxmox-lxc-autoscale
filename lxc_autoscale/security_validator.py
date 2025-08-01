"""Enhanced security validation and configuration hardening."""

import re
import os
import logging
import hashlib
import secrets
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from dataclasses import dataclass
from pathlib import Path
import ipaddress
import yaml
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64


@dataclass
class SecurityViolation:
    """Represents a security violation found during validation."""
    severity: str  # 'critical', 'high', 'medium', 'low'
    category: str  # 'injection', 'exposure', 'weak_config', etc.
    message: str
    location: str
    recommendation: str


@dataclass
class ValidationResult:
    """Result of security validation."""
    is_valid: bool
    violations: List[SecurityViolation]
    warnings: List[str]
    security_score: float  # 0-100


class ConfigurationValidator:
    """Validates configuration for security vulnerabilities."""
    
    def __init__(self):
        """Initialize configuration validator."""
        self.dangerous_patterns = [
            r'[;&|`$()]',  # Command injection patterns
            r'\.\./',      # Path traversal
            r'<script',    # XSS patterns
            r'DROP\s+TABLE',  # SQL injection patterns
            r'rm\s+-rf',   # Dangerous commands
            r'eval\s*\(',  # Code evaluation
        ]
        
        self.weak_passwords = {
            'password', '123456', 'admin', 'root', 'guest',
            'test', 'password123', 'admin123', 'qwerty'
        }
        
        self.secure_defaults = {
            'ssh_timeout': 30,
            'max_concurrent_operations': 10,
            'enable_logging': True,
            'log_level': 'INFO',
            'encryption_enabled': True
        }
    
    def validate_configuration(self, config: Dict[str, Any]) -> ValidationResult:
        """Validate configuration for security issues.
        
        Args:
            config: Configuration dictionary
            
        Returns:
            Validation result with violations and score
        """
        violations = []
        warnings = []
        
        # Validate different configuration sections
        violations.extend(self._validate_authentication_config(config))
        violations.extend(self._validate_network_config(config))
        violations.extend(self._validate_command_config(config))
        violations.extend(self._validate_file_permissions(config))
        violations.extend(self._validate_encryption_config(config))
        violations.extend(self._validate_input_validation(config))
        
        # Check for missing security configurations
        warnings.extend(self._check_missing_security_configs(config))
        
        # Calculate security score
        security_score = self._calculate_security_score(violations)
        
        is_valid = not any(v.severity in ['critical', 'high'] for v in violations)
        
        return ValidationResult(
            is_valid=is_valid,
            violations=violations,
            warnings=warnings,
            security_score=security_score
        )
    
    def _validate_authentication_config(self, config: Dict[str, Any]) -> List[SecurityViolation]:
        """Validate authentication configuration."""
        violations = []
        
        # Check for weak passwords
        for key, value in config.items():
            if 'password' in key.lower() and isinstance(value, str):
                if value.lower() in self.weak_passwords or len(value) < 8:
                    violations.append(SecurityViolation(
                        severity='high',
                        category='weak_auth',
                        message=f'Weak password detected in {key}',
                        location=key,
                        recommendation='Use strong passwords with at least 12 characters, including mixed case, numbers, and symbols'
                    ))
        
        # Check for default credentials
        default_creds = ['admin:admin', 'root:root', 'guest:guest']
        for key, value in config.items():
            if isinstance(value, str) and any(cred in value for cred in default_creds):
                violations.append(SecurityViolation(
                    severity='critical',
                    category='default_creds',
                    message=f'Default credentials detected in {key}',
                    location=key,
                    recommendation='Change default credentials immediately'
                ))
        
        # Check SSH key security
        ssh_key_path = config.get('ssh_key_path')
        if ssh_key_path:
            try:
                key_path = Path(ssh_key_path)
                if key_path.exists():
                    stat = key_path.stat()
                    # Check if key file is too permissive
                    if stat.st_mode & 0o077:  # Others can read/write
                        violations.append(SecurityViolation(
                            severity='high',
                            category='file_permissions',
                            message=f'SSH key file has overly permissive permissions: {ssh_key_path}',
                            location='ssh_key_path',
                            recommendation='Set permissions to 600 (chmod 600)'
                        ))
            except Exception as e:
                logging.warning(f"Could not check SSH key permissions: {e}")
        
        return violations
    
    def _validate_network_config(self, config: Dict[str, Any]) -> List[SecurityViolation]:
        """Validate network configuration."""
        violations = []
        
        # Validate IP addresses
        for key, value in config.items():
            if 'host' in key.lower() or 'ip' in key.lower():
                if isinstance(value, str) and value:
                    try:
                        ip = ipaddress.ip_address(value)
                        # Warn about public IPs in certain contexts
                        if ip.is_global and 'proxmox' in key.lower():
                            violations.append(SecurityViolation(
                                severity='medium',
                                category='network_exposure',
                                message=f'Public IP address used for Proxmox host: {value}',
                                location=key,
                                recommendation='Consider using VPN or private network for Proxmox access'
                            ))
                    except ValueError:
                        # Not an IP address, might be hostname
                        if value in ['localhost', '127.0.0.1', '0.0.0.0']:
                            continue
                        # Check for suspicious hostnames
                        if any(char in value for char in ['<', '>', '"', "'"]):
                            violations.append(SecurityViolation(
                                severity='medium',
                                category='injection',
                                message=f'Suspicious characters in hostname: {value}',
                                location=key,
                                recommendation='Use only valid hostname characters'
                            ))
        
        # Check port configurations
        for key, value in config.items():
            if 'port' in key.lower() and isinstance(value, int):
                if value < 1024 and value != 22:  # Privileged ports (except SSH)
                    violations.append(SecurityViolation(
                        severity='medium',
                        category='network_config',
                        message=f'Using privileged port {value} for {key}',
                        location=key,
                        recommendation='Consider using unprivileged ports (>1024) where possible'
                    ))
        
        return violations
    
    def _validate_command_config(self, config: Dict[str, Any]) -> List[SecurityViolation]:
        """Validate command configuration for injection vulnerabilities."""
        violations = []
        
        for key, value in config.items():
            if isinstance(value, str):
                # Check for command injection patterns
                for pattern in self.dangerous_patterns:
                    if re.search(pattern, value, re.IGNORECASE):
                        violations.append(SecurityViolation(
                            severity='high',
                            category='injection',
                            message=f'Potential command injection pattern found in {key}: {pattern}',
                            location=key,
                            recommendation='Sanitize input and use parameterized commands'
                        ))
                
                # Check for absolute paths in suspicious contexts
                if ('command' in key.lower() or 'path' in key.lower()) and value.startswith('/'):
                    if not os.path.exists(value):
                        violations.append(SecurityViolation(
                            severity='medium',
                            category='path_validation',
                            message=f'Path does not exist: {value}',
                            location=key,
                            recommendation='Verify path exists and is accessible'
                        ))
        
        return violations
    
    def _validate_file_permissions(self, config: Dict[str, Any]) -> List[SecurityViolation]:
        """Validate file permissions and paths."""
        violations = []
        
        sensitive_files = ['key', 'cert', 'config', 'log']
        
        for key, value in config.items():
            if isinstance(value, str) and any(sf in key.lower() for sf in sensitive_files):
                if value and os.path.exists(value):
                    try:
                        stat = os.stat(value)
                        # Check if file is world-readable
                        if stat.st_mode & 0o044:  # World or group readable
                            violations.append(SecurityViolation(
                                severity='medium',
                                category='file_permissions',
                                message=f'Sensitive file is readable by others: {value}',
                                location=key,
                                recommendation='Restrict file permissions (chmod 600 or 640)'
                            ))
                    except Exception as e:
                        logging.warning(f"Could not check permissions for {value}: {e}")
        
        return violations
    
    def _validate_encryption_config(self, config: Dict[str, Any]) -> List[SecurityViolation]:
        """Validate encryption configuration."""
        violations = []
        
        # Check if encryption is disabled
        if not config.get('encryption_enabled', True):
            violations.append(SecurityViolation(
                severity='high',
                category='encryption',
                message='Encryption is disabled',
                location='encryption_enabled',
                recommendation='Enable encryption for sensitive data'
            ))
        
        # Check for weak encryption settings
        tls_version = config.get('min_tls_version')
        if tls_version and tls_version < 1.2:
            violations.append(SecurityViolation(
                severity='high',
                category='encryption',
                message=f'Weak TLS version configured: {tls_version}',
                location='min_tls_version',
                recommendation='Use TLS 1.2 or higher'
            ))
        
        return violations
    
    def _validate_input_validation(self, config: Dict[str, Any]) -> List[SecurityViolation]:
        """Validate input validation settings."""
        violations = []
        
        # Check if input validation is disabled
        if config.get('disable_input_validation', False):
            violations.append(SecurityViolation(
                severity='critical',
                category='input_validation',
                message='Input validation is disabled',
                location='disable_input_validation',
                recommendation='Enable input validation to prevent injection attacks'
            ))
        
        # Check timeout settings
        timeouts = ['ssh_timeout', 'command_timeout', 'api_timeout']
        for timeout_key in timeouts:
            timeout_value = config.get(timeout_key)
            if timeout_value and timeout_value > 300:  # 5 minutes
                violations.append(SecurityViolation(
                    severity='medium',
                    category='dos_protection',
                    message=f'Long timeout configured: {timeout_key}={timeout_value}s',
                    location=timeout_key,
                    recommendation='Use shorter timeouts to prevent resource exhaustion'
                ))
        
        return violations
    
    def _check_missing_security_configs(self, config: Dict[str, Any]) -> List[str]:
        """Check for missing security configurations."""
        warnings = []
        
        recommended_configs = [
            'max_retry_attempts',
            'rate_limit_enabled',
            'audit_logging_enabled',
            'secure_random_enabled',
            'certificate_validation',
        ]
        
        for rec_config in recommended_configs:
            if rec_config not in config:
                warnings.append(f'Recommended security setting missing: {rec_config}')
        
        return warnings
    
    def _calculate_security_score(self, violations: List[SecurityViolation]) -> float:
        """Calculate security score based on violations.
        
        Args:
            violations: List of security violations
            
        Returns:
            Security score from 0-100
        """
        base_score = 100.0
        
        severity_penalties = {
            'critical': 25.0,
            'high': 15.0,
            'medium': 8.0,
            'low': 3.0
        }
        
        for violation in violations:
            penalty = severity_penalties.get(violation.severity, 5.0)
            base_score -= penalty
        
        return max(0.0, base_score)


class SecureConfigManager:
    """Manages secure configuration with encryption."""
    
    def __init__(self, master_key: Optional[bytes] = None):
        """Initialize secure config manager.
        
        Args:
            master_key: Master encryption key (generates one if None)
        """
        if master_key:
            self.cipher = Fernet(master_key)
        else:
            self.cipher = Fernet(Fernet.generate_key())
        
        self.validator = ConfigurationValidator()
    
    def encrypt_sensitive_value(self, value: str) -> str:
        """Encrypt a sensitive configuration value.
        
        Args:
            value: Value to encrypt
            
        Returns:
            Encrypted value as base64 string
        """
        encrypted = self.cipher.encrypt(value.encode())
        return base64.b64encode(encrypted).decode()
    
    def decrypt_sensitive_value(self, encrypted_value: str) -> str:
        """Decrypt a sensitive configuration value.
        
        Args:
            encrypted_value: Encrypted value as base64 string
            
        Returns:
            Decrypted value
        """
        encrypted_bytes = base64.b64decode(encrypted_value.encode())
        decrypted = self.cipher.decrypt(encrypted_bytes)
        return decrypted.decode()
    
    def secure_config_dict(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Secure a configuration dictionary by encrypting sensitive values.
        
        Args:
            config: Configuration dictionary
            
        Returns:
            Configuration with encrypted sensitive values
        """
        sensitive_keys = [
            'password', 'secret', 'key', 'token', 'credential'
        ]
        
        secured_config = config.copy()
        
        for key, value in config.items():
            if isinstance(value, str) and any(sk in key.lower() for sk in sensitive_keys):
                if not value.startswith('ENCRYPTED:'):
                    secured_config[key] = f"ENCRYPTED:{self.encrypt_sensitive_value(value)}"
        
        return secured_config
    
    def load_secure_config(self, config_path: str) -> Dict[str, Any]:
        """Load and decrypt a secure configuration file.
        
        Args:
            config_path: Path to configuration file
            
        Returns:
            Decrypted configuration dictionary
            
        Raises:
            FileNotFoundError: If config file doesn't exist
            yaml.YAMLError: If config file is invalid YAML
        """
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        # Decrypt encrypted values
        for key, value in config.items():
            if isinstance(value, str) and value.startswith('ENCRYPTED:'):
                encrypted_value = value[10:]  # Remove 'ENCRYPTED:' prefix
                config[key] = self.decrypt_sensitive_value(encrypted_value)
        
        return config
    
    def validate_and_secure_config(self, config: Dict[str, Any]) -> Tuple[Dict[str, Any], ValidationResult]:
        """Validate and secure a configuration.
        
        Args:
            config: Configuration dictionary
            
        Returns:
            Tuple of (secured_config, validation_result)
        """
        # First validate the configuration
        validation_result = self.validator.validate_configuration(config)
        
        # Then secure it
        secured_config = self.secure_config_dict(config)
        
        return secured_config, validation_result
    
    @staticmethod
    def generate_secure_password(length: int = 16) -> str:
        """Generate a cryptographically secure password.
        
        Args:
            length: Password length
            
        Returns:
            Secure random password
        """
        import string
        
        characters = string.ascii_letters + string.digits + "!@#$%^&*"
        password = ''.join(secrets.choice(characters) for _ in range(length))
        
        return password
    
    @staticmethod
    def hash_password(password: str, salt: Optional[bytes] = None) -> Tuple[str, bytes]:
        """Hash a password using PBKDF2.
        
        Args:
            password: Password to hash
            salt: Salt bytes (generates one if None)
            
        Returns:
            Tuple of (hashed_password_b64, salt)
        """
        if salt is None:
            salt = os.urandom(32)
        
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        
        key = kdf.derive(password.encode())
        return base64.b64encode(key).decode(), salt
    
    @staticmethod
    def verify_password(password: str, hashed_password: str, salt: bytes) -> bool:
        """Verify a password against its hash.
        
        Args:
            password: Password to verify
            hashed_password: Base64 encoded hash
            salt: Salt used for hashing
            
        Returns:
            True if password matches
        """
        try:
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=100000,
            )
            
            kdf.verify(password.encode(), base64.b64decode(hashed_password))
            return True
        except Exception:
            return False


class InputSanitizer:
    """Sanitizes and validates input to prevent injection attacks."""
    
    def __init__(self):
        """Initialize input sanitizer."""
        self.allowed_container_id_pattern = re.compile(r'^[0-9]+$')
        self.allowed_hostname_pattern = re.compile(r'^[a-zA-Z0-9.-]+$')
        self.dangerous_command_patterns = [
            r'[;&|`$()]',
            r'\.\./',
            r'rm\s+',
            r'dd\s+',
            r'mkfs',
            r'format'
        ]
    
    def sanitize_container_id(self, container_id: str) -> Optional[str]:
        """Sanitize container ID.
        
        Args:
            container_id: Container ID to sanitize
            
        Returns:
            Sanitized container ID or None if invalid
        """
        if not isinstance(container_id, str):
            return None
        
        # Remove whitespace
        container_id = container_id.strip()
        
        # Validate format
        if not self.allowed_container_id_pattern.match(container_id):
            return None
        
        # Check reasonable range
        try:
            cid = int(container_id)
            if cid < 100 or cid > 999999:
                return None
        except ValueError:
            return None
        
        return container_id
    
    def sanitize_hostname(self, hostname: str) -> Optional[str]:
        """Sanitize hostname.
        
        Args:
            hostname: Hostname to sanitize
            
        Returns:
            Sanitized hostname or None if invalid
        """
        if not isinstance(hostname, str):
            return None
        
        # Remove whitespace
        hostname = hostname.strip()
        
        # Validate format
        if not self.allowed_hostname_pattern.match(hostname):
            return None
        
        # Check length
        if len(hostname) > 253:
            return None
        
        return hostname
    
    def validate_command_safety(self, command: str) -> bool:
        """Validate that command is safe to execute.
        
        Args:
            command: Command to validate
            
        Returns:
            True if command appears safe
        """
        if not isinstance(command, str):
            return False
        
        # Check for dangerous patterns
        for pattern in self.dangerous_command_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return False
        
        # Whitelist approach for Proxmox commands
        allowed_commands = ['pct', 'qm', 'pvesh', 'pvesm', 'pvecm']
        command_parts = command.split()
        
        if not command_parts or command_parts[0] not in allowed_commands:
            return False
        
        return True
    
    def sanitize_path(self, path: str) -> Optional[str]:
        """Sanitize file path.
        
        Args:
            path: File path to sanitize
            
        Returns:
            Sanitized path or None if invalid
        """
        if not isinstance(path, str):
            return None
        
        # Remove dangerous patterns
        if '..' in path or path.startswith('/etc/passwd') or path.startswith('/etc/shadow'):
            return None
        
        # Normalize path
        try:
            normalized = os.path.normpath(path)
            return normalized
        except Exception:
            return None


# Global instances
_global_validator = ConfigurationValidator()
_global_sanitizer = InputSanitizer()


def get_security_validator() -> ConfigurationValidator:
    """Get the global security validator."""
    return _global_validator


def get_input_sanitizer() -> InputSanitizer:
    """Get the global input sanitizer."""
    return _global_sanitizer


def secure_configuration_decorator(func: Callable) -> Callable:
    """Decorator to validate configuration parameters."""
    def wrapper(config, *args, **kwargs):
        validator = get_security_validator()
        validation_result = validator.validate_configuration(config)
        
        if not validation_result.is_valid:
            critical_violations = [v for v in validation_result.violations if v.severity == 'critical']
            if critical_violations:
                raise ValueError(f"Critical security violations found: {[v.message for v in critical_violations]}")
        
        return func(config, *args, **kwargs)
    
    return wrapper