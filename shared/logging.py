"""Structured JSON logging for JobsBot components.

This module provides professional, structured JSON logging that works seamlessly
with Elasticsearch, Kibana, and Logstash. Logs are emitted to stdout as JSON,
making them easy to parse, search, and visualize in Kibana dashboards.

Usage:
    from shared.logging import get_logger
    
    logger = get_logger("scraper")
    logger.info("Processing job", extra={"job_id": "123", "chat_id": "456"})
    logger.error("Failed to scrape", extra={"error": str(e)})

Context management:
    with logger.contextualize(job_id="123", user_id="456"):
        logger.info("Processing...")  # Both job_id and user_id automatically included
"""

import os
import sys
import json
import logging
import threading
from datetime import datetime
from pythonjsonlogger import jsonlogger


# Thread-local storage for context (e.g., correlation_id, job_id)
_context = threading.local()


def _get_context():
    """Get the current thread-local context."""
    if not hasattr(_context, 'data'):
        _context.data = {}
    return _context.data


def _detect_environment():
    """Detect whether running on Lambda or Docker/EC2."""
    # Check for Lambda-specific environment variables
    if os.getenv('AWS_LAMBDA_FUNCTION_NAME'):
        return 'lambda'
    # Check for Docker (typical indicators)
    if os.path.exists('/.dockerenv') or os.getenv('DOCKER_CONTAINER'):
        return 'docker'
    return 'development'


def _get_execution_context():
    """Get execution context (execution_id for Lambda, container_id for Docker)."""
    env = _detect_environment()
    context = {}
    
    if env == 'lambda':
        # AWS Lambda provides AWS_REQUEST_ID as execution context
        execution_id = os.getenv('AWS_REQUEST_ID')
        if execution_id:
            context['execution_id'] = execution_id
    elif env == 'docker':
        # Try to get container ID from hostname or cgroup
        container_id = os.getenv('HOSTNAME')  # Docker container ID is often set as hostname
        if container_id:
            context['container_id'] = container_id[:12]  # Truncate to 12 chars like Docker short ID
    
    return context


class JobsBotJSONFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter that includes JobsBot-specific fields.
    
    Emits logs with the following structure:
    {
        "timestamp": "2026-05-20T10:30:45.123Z",
        "level": "INFO",
        "message": "...",
        "component": "scraper",
        "correlation_id": "abc123" (if set),
        "job_id": "456" (if set),
        "user_id": "789" (if set),
        "execution_id": "xyz" (Lambda only, if available),
        "container_id": "abc123def456" (Docker only, if available),
        "environment": "lambda|docker|development"
    }
    """
    
    def add_fields(self, log_record, record, message_dict):
        """Add custom fields to JSON log record."""
        # Standard fields
        log_record['timestamp'] = datetime.utcnow().isoformat() + 'Z'
        log_record['level'] = record.levelname
        log_record['message'] = message_dict.get('message', record.getMessage())
        
        # Component (passed during logger creation)
        if 'component' in message_dict:
            log_record['component'] = message_dict.pop('component')
        
        # Environment detection
        environment = _detect_environment()
        log_record['environment'] = environment
        
        # Execution context (execution_id for Lambda, container_id for Docker)
        log_record.update(_get_execution_context())
        
        # Thread-local context fields (correlation_id, job_id, user_id, etc.)
        context = _get_context()
        for key, value in context.items():
            if key not in log_record:
                log_record[key] = value
        
        # Any extra fields passed via logger.info(..., extra={"key": "value"})
        # These are automatically included by the base class
        super().add_fields(log_record, record, message_dict)


class ContextualLogger:
    """Wrapper around standard logger that supports context injection.
    
    Allows setting correlation_id, job_id, user_id, etc. that are automatically
    included in all log records within a thread.
    """
    
    def __init__(self, logger, component):
        self.logger = logger
        self.component = component
    
    def debug(self, message, extra=None):
        """Log debug message."""
        extra_dict = extra or {}
        extra_dict['component'] = self.component
        self.logger.debug(message, extra=extra_dict)
    
    def info(self, message, extra=None):
        """Log info message."""
        extra_dict = extra or {}
        extra_dict['component'] = self.component
        self.logger.info(message, extra=extra_dict)
    
    def warning(self, message, extra=None):
        """Log warning message."""
        extra_dict = extra or {}
        extra_dict['component'] = self.component
        self.logger.warning(message, extra=extra_dict)
    
    def error(self, message, extra=None):
        """Log error message."""
        extra_dict = extra or {}
        extra_dict['component'] = self.component
        self.logger.error(message, extra=extra_dict)
    
    def critical(self, message, extra=None):
        """Log critical message."""
        extra_dict = extra or {}
        extra_dict['component'] = self.component
        self.logger.critical(message, extra=extra_dict)
    
    def set_context(self, **kwargs):
        """Set thread-local context variables (e.g., correlation_id, job_id)."""
        context = _get_context()
        context.update(kwargs)
    
    def clear_context(self):
        """Clear all thread-local context variables."""
        _context.data = {}
    
    def get_context(self):
        """Get current thread-local context."""
        return _get_context().copy()
    
    def contextualize(self, **kwargs):
        """Context manager for setting context within a scope."""
        return _ContextManager(self, **kwargs)


class _ContextManager:
    """Context manager for temporary context settings."""
    
    def __init__(self, logger, **kwargs):
        self.logger = logger
        self.kwargs = kwargs
        self.old_context = None
    
    def __enter__(self):
        # Save old context
        self.old_context = _get_context().copy()
        # Set new context
        self.logger.set_context(**self.kwargs)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        # Restore old context
        _context.data = self.old_context
        return False


def get_logger(component_name):
    """Factory function to create a configured logger for a component.
    
    Args:
        component_name: Name of the component (e.g., 'scraper', 'bot', 'dispatcher')
    
    Returns:
        ContextualLogger: A logger configured for JSON output with context support
    
    Example:
        logger = get_logger('scraper')
        logger.info('Starting job scraping', extra={'job_id': '123'})
    """
    # Create base logger
    logger = logging.getLogger(f'jobsbot.{component_name}')
    
    # Only configure if not already configured (avoid duplicate handlers)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        # Create stdout handler
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        
        # Create JSON formatter
        formatter = JobsBotJSONFormatter()
        handler.setFormatter(formatter)
        
        logger.addHandler(handler)
        logger.propagate = False
    
    # Wrap in contextual logger for easier API
    return ContextualLogger(logger, component_name)


# Backward compatibility: also export for direct use if needed
__all__ = ['get_logger', 'ContextualLogger']
