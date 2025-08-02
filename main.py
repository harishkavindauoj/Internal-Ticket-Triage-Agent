"""
FastAPI application entry point for the Ticket Triage Agent.
Production-ready setup with middleware, error handling, and monitoring.
"""

import os
import sys
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.openapi.utils import get_openapi
import uvicorn
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from routers.webhook import router as webhook_router
from db.lookup import db_manager
from utils.logger import setup_logging, logger, metrics, get_health_status
from models.ticket import HealthCheckResponse
from dotenv import load_dotenv

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager for startup and shutdown procedures.
    Handles database initialization and cleanup.
    """
    # Startup
    logger.info("Starting Ticket Triage Agent")

    try:
        # Initialize database
        await db_manager.initialize_database()
        logger.info("Database initialized successfully")

        # Setup logging
        setup_logging()

        # Log startup completion
        logger.info(
            "Application startup completed",
            environment=os.getenv("ENVIRONMENT", "development"),
            version="1.0.0"
        )

        yield

    except Exception as e:
        logger.error("Application startup failed", error=str(e))
        sys.exit(1)

    finally:
        # Shutdown
        logger.info("Shutting down Ticket Triage Agent")

        try:
            # Close database connections
            await db_manager.close()
            logger.info("Database connections closed")

        except Exception as e:
            logger.error("Error during shutdown", error=str(e))


# Create FastAPI application
app = FastAPI(
    title="Internal Ticket Triage Agent",
    description="""
    Production-ready ticket classification and routing system.

    ## Features

    * **AI Classification**: Uses Google Gemini to classify tickets into departments
    * **Smart Routing**: Routes tickets to appropriate teams via REST APIs
    * **Multi-System Support**: Integrates with Jira, Freshservice, Slack, and more
    * **n8n Compatible**: Designed for seamless n8n workflow integration
    * **Monitoring**: Built-in Prometheus metrics and health checks
    * **Resilient**: Retry logic, circuit breakers, and fallback handling

    ## Workflow

    1. Receive ticket via webhook POST to `/webhook/ticket`
    2. Classify ticket using AI (Gemini 1.5 Pro)
    3. Look up team mapping in database
    4. Route to external system (Jira, Freshservice, etc.)
    5. Return ticket ID and status

    ## n8n Integration

    Use the `/webhook/ticket` endpoint in your n8n workflows:
    - Method: POST
    - Content-Type: application/json
    - Body: See TicketCreateRequest schema

    The system returns immediate response with ticket ID and processing status.
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    contact={
        "name": "IT Operations Team",
        "email": "it-ops@company.com"
    },
    license_info={
        "name": "MIT License",
        "url": "https://opensource.org/licenses/MIT"
    }
)

# Add middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)


# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log HTTP requests with timing and response status"""
    import time

    start_time = time.time()

    # Log request
    logger.info(
        "HTTP request received",
        method=request.method,
        url=str(request.url),
        client_ip=request.client.host if request.client else "unknown",
        user_agent=request.headers.get("user-agent", "unknown")
    )

    # Process request
    response = await call_next(request)

    # Log response
    process_time = time.time() - start_time
    logger.info(
        "HTTP request completed",
        method=request.method,
        url=str(request.url),
        status_code=response.status_code,
        process_time_ms=round(process_time * 1000, 2)
    )

    # Add timing header
    response.headers["X-Process-Time"] = str(process_time)

    return response


# Exception handlers
@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    """Custom 404 handler"""
    return JSONResponse(
        status_code=404,
        content={
            "error": "Not Found",
            "message": "The requested endpoint was not found",
            "path": str(request.url.path),
            "suggestion": "Check the API documentation at /docs"
        }
    )


@app.exception_handler(500)
async def internal_server_error_handler(request: Request, exc: Exception):
    """Custom 500 handler"""
    logger.error(
        "Internal server error",
        error=str(exc),
        path=str(request.url.path),
        method=request.method
    )

    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "message": "An unexpected error occurred",
            "request_id": getattr(request.state, "request_id", "unknown")
        }
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Custom HTTP exception handler"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.__class__.__name__,
            "message": exc.detail,
            "status_code": exc.status_code
        }
    )


# Include routers
app.include_router(webhook_router)


# Root endpoints
@app.get("/", response_model=Dict[str, Any])
async def root():
    """Root endpoint with API information"""
    return {
        "name": "Internal Ticket Triage Agent",
        "version": "1.0.0",
        "description": "AI-powered ticket classification and routing system",
        "docs_url": "/docs",
        "health_check": "/health",
        "metrics": "/metrics",
        "webhook_endpoint": "/webhook/ticket",
        "environment": os.getenv("ENVIRONMENT", "development")
    }


@app.get("/health", response_model=HealthCheckResponse)
async def health_check():
    """
    Application health check endpoint.
    Returns overall health status and dependency information.
    """
    try:
        health_data = get_health_status()

        # Check database
        try:
            await db_manager.get_metrics()
            health_data["dependencies"] = {"database": "healthy"}
        except Exception as e:
            health_data["dependencies"] = {"database": f"unhealthy: {str(e)}"}
            health_data["status"] = "degraded"

        return HealthCheckResponse(**health_data)

    except Exception as e:
        logger.error("Health check failed", error=str(e))
        raise HTTPException(
            status_code=503,
            detail="Health check failed"
        )


@app.get("/metrics")
async def get_metrics():
    """
    Prometheus metrics endpoint.
    Returns metrics in Prometheus format for monitoring.
    """
    if not metrics.metrics_enabled:
        raise HTTPException(
            status_code=404,
            detail="Metrics are disabled"
        )

    # Generate Prometheus metrics
    metrics_data = generate_latest()

    return Response(
        content=metrics_data,
        media_type=CONTENT_TYPE_LATEST
    )


@app.get("/config", response_model=Dict[str, Any])
async def get_config():
    """
    Get application configuration (non-sensitive values only).
    Useful for debugging and monitoring.
    """
    return {
        "environment": os.getenv("ENVIRONMENT", "development"),
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
        "metrics_enabled": os.getenv("PROMETHEUS_METRICS_ENABLED", "true"),
        "database_type": "sqlite" if "sqlite" in os.getenv("DATABASE_URL", "") else "postgresql",
        "version": "1.0.0",
        "features": {
            "ai_classification": True,
            "multi_system_routing": True,
            "prometheus_metrics": metrics.metrics_enabled,
            "circuit_breakers": True,
            "retry_logic": True
        }
    }


# Custom OpenAPI schema
def custom_openapi():
    """Generate custom OpenAPI schema with additional information"""
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title="Internal Ticket Triage Agent API",
        version="1.0.0",
        description=app.description,
        routes=app.routes,
    )

    # Add custom schema information
    openapi_schema["info"]["x-logo"] = {
        "url": "https://fastapi.tiangolo.com/img/logo-margin/logo-teal.png"
    }

    # Add server information
    openapi_schema["servers"] = [
        {
            "url": "/",
            "description": "Development server"
        }
    ]

    # Add example webhook payload
    openapi_schema["components"]["examples"] = {
        "SampleTicket": {
            "summary": "Sample IT ticket",
            "description": "Example ticket for VPN connectivity issue",
            "value": {
                "title": "VPN not working after update",
                "description": "Since the latest Windows update, I cannot connect to the company VPN. The connection times out after about 30 seconds. I've tried restarting my computer and reinstalling the VPN client, but the issue persists.",
                "email": "john.doe@company.com",
                "priority": "medium",
                "metadata": {
                    "source": "n8n_workflow",
                    "user_department": "Engineering",
                    "os": "Windows 11"
                }
            }
        }
    }

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi

# Development server configuration
if __name__ == "__main__":
    load_dotenv()

    # Configure uvicorn for development
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=os.getenv("ENVIRONMENT", "development") == "development",
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        access_log=True,
        workers=1 if os.getenv("ENVIRONMENT") == "development" else 4
    )
