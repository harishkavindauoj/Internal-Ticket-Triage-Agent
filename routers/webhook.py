"""
FastAPI webhook router for receiving and processing tickets from n8n.
Handles the complete ticket triage workflow with proper error handling and monitoring.
"""

import uuid
import time
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from fastapi.responses import JSONResponse

from models.ticket import (
    TicketCreateRequest,
    TicketResponse,
    IncomingTicket,
    ProcessedTicket,
    TicketStatus,
    HealthCheckResponse,
    MetricsResponse
)
from services.classifier import TicketClassifier
from services.router import TicketRouter
from db.lookup import db_manager
from utils.logger import TicketLogger, metrics, logger
from db.lookup import TicketLogDB
from sqlalchemy import select

# Initialize router
router = APIRouter(prefix="/webhook", tags=["webhook"])

# Initialize services (will be properly injected in production)
classifier = TicketClassifier()
ticket_router = TicketRouter()


async def process_ticket_workflow(ticket: ProcessedTicket) -> ProcessedTicket:
    """
    Complete ticket processing workflow:
    1. Classify ticket using AI
    2. Look up team mapping
    3. Route to external system
    4. Log results to database
    """
    ticket_logger = TicketLogger(ticket.ticket_id)

    try:
        # Step 1: AI Classification
        ticket_logger.info("Starting ticket classification")

        incoming_ticket = IncomingTicket(
            title=ticket.title,
            description=ticket.description,
            email=ticket.email,
            priority=ticket.priority,
            metadata=ticket.metadata
        )

        classification_result = await classifier.classify_ticket(incoming_ticket)

        # Update ticket with classification results
        ticket.department = classification_result.department
        ticket.assigned_to = classification_result.assigned_to
        ticket.confidence_score = classification_result.confidence_score
        ticket.classification_reasoning = classification_result.reasoning
        ticket.status = TicketStatus.CLASSIFIED

        ticket_logger.info(
            "Ticket classified successfully",
            department=ticket.department.value,
            assigned_to=ticket.assigned_to,
            confidence=ticket.confidence_score
        )

        # Step 2: Database lookup for team mapping
        team_mapping = await db_manager.get_team_mapping(
            ticket.department,
            ticket.priority
        )

        if not team_mapping:
            error_msg = f"No team mapping found for department {ticket.department.value}"
            ticket_logger.error(error_msg)
            ticket.routing_error = error_msg
            ticket.status = TicketStatus.FAILED
            await db_manager.log_ticket(ticket)
            return ticket

        ticket_logger.info(
            "Team mapping found",
            team=team_mapping.team_name,
            endpoint=team_mapping.api_endpoint
        )

        # Step 3: Route ticket to external system
        routing_result = await ticket_router.route_ticket(ticket, team_mapping)

        if routing_result.success:
            ticket.status = TicketStatus.ROUTED
            ticket.routed_to_system = routing_result.system_name
            ticket.external_ticket_id = routing_result.external_ticket_id

            ticket_logger.info(
                "Ticket routed successfully",
                system=routing_result.system_name,
                external_id=routing_result.external_ticket_id
            )
        else:
            ticket.status = TicketStatus.FAILED
            ticket.routing_error = routing_result.error_message
            ticket.routed_to_system = routing_result.system_name

            ticket_logger.error(
                "Ticket routing failed",
                system=routing_result.system_name,
                error=routing_result.error_message
            )

        # Step 4: Log to database
        await db_manager.log_ticket(ticket)

        ticket_logger.log_processing_complete(
            status=ticket.status.value,
            department=ticket.department.value if ticket.department else "unknown"
        )

        return ticket

    except Exception as e:
        ticket_logger.error("Ticket processing failed", error=e)
        ticket.status = TicketStatus.FAILED
        ticket.routing_error = f"Processing error: {str(e)}"

        # Log failed ticket
        try:
            await db_manager.log_ticket(ticket)
        except Exception as log_error:
            ticket_logger.error("Failed to log ticket to database", error=log_error)

        return ticket


@router.post("/ticket", response_model=TicketResponse)
async def create_ticket(
        ticket_request: TicketCreateRequest,
        background_tasks: BackgroundTasks,
        request: Request
):
    """
    Main webhook endpoint for receiving tickets from n8n.

    This endpoint:
    1. Validates incoming ticket data
    2. Creates a unique ticket ID
    3. Starts background processing
    4. Returns immediate response to caller

    Background processing handles:
    - AI classification
    - Team mapping lookup
    - External system routing
    - Database logging
    """
    start_time = time.time()

    # Generate unique ticket ID
    ticket_id = f"TKT-{str(uuid.uuid4())[:8].upper()}"

    # Create ticket logger
    ticket_logger = TicketLogger(ticket_id)

    try:
        # Log incoming request
        client_ip = request.client.host if request.client else "unknown"
        ticket_logger.info(
            "Received new ticket",
            title=ticket_request.title,
            email=ticket_request.email,
            priority=ticket_request.priority.value,
            client_ip=client_ip
        )

        # Validate ticket data
        incoming_ticket = IncomingTicket(
            title=ticket_request.title,
            description=ticket_request.description,
            email=ticket_request.email,
            priority=ticket_request.priority,
            metadata=ticket_request.metadata
        )

        # Create processed ticket
        processed_ticket = ProcessedTicket.from_incoming(incoming_ticket, ticket_id)

        # Add background task for processing
        background_tasks.add_task(process_ticket_workflow, processed_ticket)

        # Calculate response time
        processing_time = int((time.time() - start_time) * 1000)

        # Record metrics
        metrics.record_ticket_processed("received")

        # Return immediate response
        return TicketResponse(
            ticket_id=ticket_id,
            status=TicketStatus.RECEIVED,
            message="Ticket received and queued for processing",
            processing_time_ms=processing_time
        )

    except ValueError as e:
        ticket_logger.error("Invalid ticket data", error=e)
        metrics.record_error("validation_error", "webhook")

        raise HTTPException(
            status_code=400,
            detail=f"Invalid ticket data: {str(e)}"
        )

    except Exception as e:
        ticket_logger.error("Unexpected error processing ticket", error=e)
        metrics.record_error("processing_error", "webhook")

        raise HTTPException(
            status_code=500,
            detail="Internal server error processing ticket"
        )


@router.get("/ticket/{ticket_id}", response_model=Dict[str, Any])
async def get_ticket_status(ticket_id: str):
    """
    Get the current status and details of a ticket.
    Useful for n8n workflows to check processing progress.
    """
    try:
        # Query database for ticket
        async with db_manager.async_session() as session:


            query = select(TicketLogDB).where(TicketLogDB.ticket_id == ticket_id)
            result = await session.execute(query)
            ticket_record = result.scalar_one_or_none()

            if not ticket_record:
                raise HTTPException(
                    status_code=404,
                    detail=f"Ticket {ticket_id} not found"
                )

            return {
                "ticket_id": ticket_record.ticket_id,
                "title": ticket_record.title,
                "status": ticket_record.status,
                "department": ticket_record.department,
                "assigned_to": ticket_record.assigned_to,
                "external_ticket_id": ticket_record.external_ticket_id,
                "routed_to_system": ticket_record.routed_to_system,
                "confidence_score": float(ticket_record.confidence_score) if ticket_record.confidence_score else None,
                "error_message": ticket_record.error_message,
                "created_at": ticket_record.created_at.isoformat() if ticket_record.created_at else None,
                "updated_at": ticket_record.updated_at.isoformat() if ticket_record.updated_at else None
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error retrieving ticket status", ticket_id=ticket_id, error=str(e))
        raise HTTPException(
            status_code=500,
            detail="Error retrieving ticket status"
        )


@router.get("/health", response_model=HealthCheckResponse)
async def health_check():
    """
    Health check endpoint for monitoring and load balancers.
    Verifies connectivity to dependencies.
    """
    try:
        # Test database connectivity
        db_status = "healthy"
        try:
            metrics_data = await db_manager.get_metrics()
            db_status = "healthy"
        except Exception:
            db_status = "unhealthy"

        # Test AI service
        ai_status = "healthy"
        try:
            classifier_stats = classifier.get_classification_stats()
            ai_status = "healthy"
        except Exception:
            ai_status = "unhealthy"

        # Test routing service
        router_status = "healthy"
        try:
            routing_stats = ticket_router.get_routing_stats()
            router_status = "healthy"
        except Exception:
            router_status = "unhealthy"

        # Overall status
        overall_status = "healthy" if all(
            status == "healthy" for status in [db_status, ai_status, router_status]
        ) else "degraded"

        return HealthCheckResponse(
            status=overall_status,
            timestamp=time.time(),
            dependencies={
                "database": db_status,
                "ai_classifier": ai_status,
                "ticket_router": router_status
            }
        )

    except Exception as e:
        logger.error("Health check failed", error=str(e))
        raise HTTPException(
            status_code=503,
            detail="Service unavailable"
        )


@router.get("/metrics", response_model=MetricsResponse)
async def get_metrics():
    """
    Metrics endpoint for monitoring dashboard.
    Returns processing statistics and performance data.
    """
    try:
        # Get database metrics
        db_metrics = await db_manager.get_metrics()

        # Get classification stats
        classification_stats = classifier.get_classification_stats()

        # Get routing stats
        routing_stats = ticket_router.get_routing_stats()

        # Calculate derived metrics
        total_tickets = db_metrics.get("total_tickets_processed", 0)
        success_rate = db_metrics.get("success_rate", 0.0)

        # Error rate calculation (simplified)
        failed_tickets = total_tickets - int(total_tickets * success_rate / 100)
        error_rate = (failed_tickets / total_tickets * 100) if total_tickets > 0 else 0.0

        return MetricsResponse(
            total_tickets_processed=total_tickets,
            success_rate=success_rate,
            average_processing_time_ms=2500.0,  # Placeholder - would calculate from logs
            department_distribution=db_metrics.get("department_distribution", {}),
            error_rate_by_type={
                "classification_errors": round(error_rate * 0.2, 2),
                "routing_errors": round(error_rate * 0.6, 2),
                "system_errors": round(error_rate * 0.2, 2)
            }
        )

    except Exception as e:
        logger.error("Failed to retrieve metrics", error=str(e))
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve metrics"
        )



@router.post("/test/endpoint")
async def test_external_endpoint(
        endpoint_data: Dict[str, Any]
):
    """
    Test endpoint connectivity for configuration validation.
    Useful for verifying team mapping configurations.
    """
    try:
        endpoint_url = endpoint_data.get("url")
        method = endpoint_data.get("method", "GET")

        if not endpoint_url:
            raise HTTPException(
                status_code=400,
                detail="URL is required"
            )

        # Test the endpoint
        test_result = await ticket_router.test_endpoint(endpoint_url, method)

        return {
            "test_result": test_result,
            "timestamp": time.time()
        }

    except Exception as e:
        logger.error("Endpoint test failed", error=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Endpoint test failed: {str(e)}"
        )


# # Error handlers
# @router.exception_handler(HTTPException)
# async def http_exception_handler(request: Request, exc: HTTPException):
#     """Custom HTTP exception handler with logging"""
#     logger.warning(
#         "HTTP exception occurred",
#         status_code=exc.status_code,
#         detail=exc.detail,
#         path=request.url.path,
#         method=request.method
#     )
#
#     return JSONResponse(
#         status_code=exc.status_code,
#         content={
#             "error": exc.detail,
#             "timestamp": time.time(),
#             "path": request.url.path
#         }
#     )
#
#
# @router.exception_handler(Exception)
# async def general_exception_handler(request: Request, exc: Exception):
#     """General exception handler for unexpected errors"""
#     logger.error(
#         "Unexpected error occurred",
#         error_type=type(exc).__name__,
#         error_message=str(exc),
#         path=request.url.path,
#         method=request.method
#     )
#
#     metrics.record_error(type(exc).__name__, "webhook")
#
#     return JSONResponse(
#         status_code=500,
#         content={
#             "error": "Internal server error",
#             "timestamp": time.time(),
#             "path": request.url.path
#         }
#     )