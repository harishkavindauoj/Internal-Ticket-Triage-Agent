"""
Logging utilities with Prometheus metrics and retry decorators.
Provides structured logging and monitoring for production environments.
"""

import os
import time
import logging
import functools
from typing import Any, Callable, Optional, Dict
from datetime import datetime
from contextlib import contextmanager

import structlog
from prometheus_client import Counter, Histogram, Gauge, start_http_server
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)

# Configure structured logging
logging.basicConfig(
    format="%(message)s",
    stream=None,
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper())
)

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Prometheus Metrics
TICKET_COUNTER = Counter(
    'tickets_total',
    'Total number of tickets processed',
    ['status', 'department']
)

TICKET_PROCESSING_TIME = Histogram(
    'ticket_processing_seconds',
    'Time spent processing tickets',
    ['operation', 'status']
)

CLASSIFICATION_ACCURACY = Gauge(
    'classification_confidence',
    'AI classification confidence scores',
    ['department']
)

API_REQUEST_COUNTER = Counter(
    'api_requests_total',
    'Total API requests made',
    ['endpoint', 'method', 'status_code']
)

API_REQUEST_DURATION = Histogram(
    'api_request_duration_seconds',
    'API request duration',
    ['endpoint', 'method']
)

ACTIVE_CONNECTIONS = Gauge(
    'active_database_connections',
    'Number of active database connections'
)

ERROR_COUNTER = Counter(
    'errors_total',
    'Total number of errors',
    ['error_type', 'component']
)


class MetricsManager:
    """
    Centralized metrics management for Prometheus monitoring.
    Tracks ticket processing, API calls, and system health.
    """

    def __init__(self, metrics_port: int = 8001):
        self.metrics_port = metrics_port
        self.metrics_enabled = os.getenv("PROMETHEUS_METRICS_ENABLED", "true").lower() == "true"

        if self.metrics_enabled:
            # Start Prometheus metrics server
            start_http_server(self.metrics_port)
            logger.info("Prometheus metrics server started", port=self.metrics_port)

    def record_ticket_processed(self, status: str, department: str = "unknown"):
        """Record ticket processing metrics"""
        if self.metrics_enabled:
            TICKET_COUNTER.labels(status=status, department=department).inc()

    def record_classification_confidence(self, department: str, confidence: float):
        """Record AI classification confidence"""
        if self.metrics_enabled:
            CLASSIFICATION_ACCURACY.labels(department=department).set(confidence)

    def record_api_request(self, endpoint: str, method: str, status_code: int, duration: float):
        """Record external API request metrics"""
        if self.metrics_enabled:
            API_REQUEST_COUNTER.labels(
                endpoint=endpoint,
                method=method,
                status_code=str(status_code)
            ).inc()
            API_REQUEST_DURATION.labels(endpoint=endpoint, method=method).observe(duration)

    def record_error(self, error_type: str, component: str):
        """Record error occurrence"""
        if self.metrics_enabled:
            ERROR_COUNTER.labels(error_type=error_type, component=component).inc()

    def set_active_connections(self, count: int):
        """Update active database connections gauge"""
        if self.metrics_enabled:
            ACTIVE_CONNECTIONS.set(count)


# Global metrics manager
metrics = MetricsManager()


def timing_decorator(operation: str):
    """
    Decorator to measure operation timing and record metrics.

    Args:
        operation: Name of the operation being timed
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            status = "success"

            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                status = "error"
                metrics.record_error(type(e).__name__, operation)
                raise
            finally:
                duration = time.time() - start_time
                TICKET_PROCESSING_TIME.labels(operation=operation, status=status).observe(duration)

                logger.info(
                    "Operation completed",
                    operation=operation,
                    duration_ms=round(duration * 1000, 2),
                    status=status
                )

        # Return appropriate wrapper based on function type
        return async_wrapper if hasattr(func, '__code__') and 'async' in str(func.__code__) else sync_wrapper

    return decorator


def retry_with_backoff(
        max_attempts: int = 3,
        backoff_factor: float = 2,
        exceptions: tuple = (Exception,)
):
    """
    Retry decorator with exponential backoff for production resilience.

    Args:
        max_attempts: Maximum number of retry attempts
        backoff_factor: Exponential backoff multiplier
        exceptions: Tuple of exceptions to retry on
    """

    def decorator(func: Callable) -> Callable:
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=60, exp_base=backoff_factor),
            retry=retry_if_exception_type(exceptions),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True
        )
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except exceptions as e:
                logger.error(
                    "Retry attempt failed",
                    function=func.__name__,
                    error=str(e),
                    error_type=type(e).__name__
                )
                raise

        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=60, exp_base=backoff_factor),
            retry=retry_if_exception_type(exceptions),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True
        )
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except exceptions as e:
                logger.error(
                    "Retry attempt failed",
                    function=func.__name__,
                    error=str(e),
                    error_type=type(e).__name__
                )
                raise

        # Return appropriate wrapper based on function type
        return async_wrapper if hasattr(func, '__code__') and 'async' in str(func.__code__) else sync_wrapper

    return decorator


@contextmanager
def log_context(**kwargs):
    """
    Context manager for adding structured logging context.

    Usage:
        with log_context(ticket_id="123", operation="classify"):
            logger.info("Processing ticket")
    """
    bound_logger = logger.bind(**kwargs)
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(**kwargs)

    try:
        yield bound_logger
    finally:
        structlog.contextvars.clear_contextvars()


class TicketLogger:
    """
    Specialized logger for ticket processing workflow.
    Provides consistent logging patterns across the application.
    """

    def __init__(self, ticket_id: str):
        self.ticket_id = ticket_id
        self.logger = logger.bind(ticket_id=ticket_id)
        self.start_time = time.time()

    def info(self, message: str, **kwargs):
        """Log info message with ticket context"""
        self.logger.info(message, **kwargs)

    def warning(self, message: str, **kwargs):
        """Log warning message with ticket context"""
        self.logger.warning(message, **kwargs)

    def error(self, message: str, error: Exception = None, **kwargs):
        """Log error message with ticket context and exception details"""
        if error:
            kwargs.update({
                "error_type": type(error).__name__,
                "error_message": str(error)
            })
        self.logger.error(message, **kwargs)

    def debug(self, message: str, **kwargs):
        """Log debug message with ticket context"""
        self.logger.debug(message, **kwargs)

    def log_classification(self, department: str, confidence: float, reasoning: str):
        """Log AI classification results"""
        self.logger.info(
            "Ticket classified",
            department=department,
            confidence=confidence,
            reasoning=reasoning[:200] + "..." if len(reasoning) > 200 else reasoning
        )
        metrics.record_classification_confidence(department, confidence)

    def log_routing_success(self, system: str, external_id: str, duration_ms: int):
        """Log successful ticket routing"""
        self.logger.info(
            "Ticket routed successfully",
            system=system,
            external_ticket_id=external_id,
            routing_duration_ms=duration_ms
        )
        metrics.record_ticket_processed("routed", "unknown")

    def log_routing_failure(self, system: str, error: str, duration_ms: int):
        """Log failed ticket routing"""
        self.logger.error(
            "Ticket routing failed",
            system=system,
            error=error,
            routing_duration_ms=duration_ms
        )
        metrics.record_ticket_processed("failed", "unknown")
        metrics.record_error("routing_error", "router")

    def log_processing_complete(self, status: str, department: str = "unknown"):
        """Log completion of ticket processing"""
        total_duration = (time.time() - self.start_time) * 1000
        self.logger.info(
            "Ticket processing completed",
            status=status,
            department=department,
            total_duration_ms=round(total_duration, 2)
        )
        metrics.record_ticket_processed(status, department)


class APICallLogger:
    """
    Logger for external API calls with automatic metrics recording.
    Tracks request/response timing and status codes.
    """

    def __init__(self, endpoint: str, method: str):
        self.endpoint = endpoint
        self.method = method
        self.start_time = time.time()
        self.logger = logger.bind(endpoint=endpoint, method=method)

    def log_request(self, payload_size: int = None, **kwargs):
        """Log outgoing API request"""
        log_data = {"action": "api_request_sent"}
        if payload_size:
            log_data["payload_size_bytes"] = payload_size
        log_data.update(kwargs)
        self.logger.info("API request sent", **log_data)

    def log_response(self, status_code: int, response_size: int = None, **kwargs):
        """Log API response with metrics"""
        duration = time.time() - self.start_time

        log_data = {
            "action": "api_response_received",
            "status_code": status_code,
            "duration_ms": round(duration * 1000, 2)
        }
        if response_size:
            log_data["response_size_bytes"] = response_size
        log_data.update(kwargs)

        # Log at appropriate level based on status code
        if 200 <= status_code < 300:
            self.logger.info("API request successful", **log_data)
        elif 400 <= status_code < 500:
            self.logger.warning("API request client error", **log_data)
        else:
            self.logger.error("API request server error", **log_data)

        # Record metrics
        metrics.record_api_request(self.endpoint, self.method, status_code, duration)

    def log_error(self, error: Exception):
        """Log API request error"""
        duration = time.time() - self.start_time
        self.logger.error(
            "API request failed",
            error_type=type(error).__name__,
            error_message=str(error),
            duration_ms=round(duration * 1000, 2)
        )
        metrics.record_error(type(error).__name__, "api_client")


import asyncio


def setup_logging():
    """Initialize logging configuration for the application"""
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper()))

    # Reduce noise from external libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)

    logger.info(
        "Logging configured",
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        metrics_enabled=os.getenv("PROMETHEUS_METRICS_ENABLED", "true"),
        environment=os.getenv("ENVIRONMENT", "development")
    )


# Health check helpers
def get_health_status() -> Dict[str, Any]:
    """Get current application health status"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0",
        "environment": os.getenv("ENVIRONMENT", "development"),
        "metrics_enabled": os.getenv("PROMETHEUS_METRICS_ENABLED", "true").lower() == "true"
    }


def timing_decorator(operation: str):
    """
    Decorator to measure operation timing and record metrics.

    Args:
        operation: Name of the operation being timed
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            status = "success"
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                status = "error"
                metrics.record_error(type(e).__name__, operation)
                raise
            finally:
                duration = time.time() - start_time
                TICKET_PROCESSING_TIME.labels(operation=operation, status=status).observe(duration)
                logger.info(
                    "Operation completed",
                    operation=operation,
                    duration_ms=round(duration * 1000, 2),
                    status=status
                )

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            status = "success"
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                status = "error"
                metrics.record_error(type(e).__name__, operation)
                raise
            finally:
                duration = time.time() - start_time
                TICKET_PROCESSING_TIME.labels(operation=operation, status=status).observe(duration)
                logger.info(
                    "Operation completed",
                    operation=operation,
                    duration_ms=round(duration * 1000, 2),
                    status=status
                )

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator
