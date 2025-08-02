"""
Ticket and team mapping data models using dataclasses for minimal boilerplate.
Includes validation and serialization methods for API compatibility.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, EmailStr, Field


class TicketPriority(str, Enum):
    """Ticket priority levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TicketStatus(str, Enum):
    """Ticket processing status"""
    RECEIVED = "received"
    CLASSIFIED = "classified"
    ROUTED = "routed"
    FAILED = "failed"


class DepartmentType(str, Enum):
    """Available departments for ticket routing"""
    IT = "IT"
    HR = "HR"
    FINANCE = "FINANCE"
    FACILITIES = "FACILITIES"
    LEGAL = "LEGAL"
    SECURITY = "SECURITY"
    GENERAL = "GENERAL"


@dataclass
class IncomingTicket:
    """
    Raw ticket data received from external systems (n8n webhook).
    Uses dataclass to minimize boilerplate while providing structure.
    """
    title: str
    description: str
    email: str
    priority: TicketPriority = TicketPriority.MEDIUM
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Validate and clean incoming data"""
        if not self.title.strip():
            raise ValueError("Ticket title cannot be empty")
        if not self.description.strip():
            raise ValueError("Ticket description cannot be empty")
        if not self.email.strip():
            raise ValueError("Email cannot be empty")


@dataclass
class ProcessedTicket:
    """
    Ticket after classification and enrichment.
    Contains all original data plus AI classification results.
    """
    # Original ticket data
    title: str
    description: str
    email: str
    priority: TicketPriority
    metadata: Dict[str, Any]

    # Processing data
    ticket_id: str
    created_at: datetime
    status: TicketStatus = TicketStatus.RECEIVED

    # AI Classification results
    department: Optional[DepartmentType] = None
    assigned_to: Optional[str] = None
    confidence_score: Optional[float] = None
    classification_reasoning: Optional[str] = None

    # Routing results
    routed_to_system: Optional[str] = None
    external_ticket_id: Optional[str] = None
    routing_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        result = asdict(self)
        # Convert datetime to ISO format
        if isinstance(result.get('created_at'), datetime):
            result['created_at'] = result['created_at'].isoformat()
        return result

    @classmethod
    def from_incoming(cls, incoming: IncomingTicket, ticket_id: str) -> 'ProcessedTicket':
        """Create ProcessedTicket from IncomingTicket"""
        return cls(
            title=incoming.title,
            description=incoming.description,
            email=incoming.email,
            priority=incoming.priority,
            metadata=incoming.metadata,
            ticket_id=ticket_id,
            created_at=datetime.utcnow()
        )


@dataclass
class TeamMapping:
    """
    Database model for team routing configuration.
    Maps departments to specific teams and their API endpoints.
    """
    id: Optional[int] = None
    department: DepartmentType = DepartmentType.GENERAL
    team_name: str = "default_team"
    api_endpoint: str = ""
    api_method: str = "POST"
    api_headers: Dict[str, str] = field(default_factory=dict)
    priority_threshold: TicketPriority = TicketPriority.LOW
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for database operations"""
        result = asdict(self)
        # Handle datetime serialization
        for field_name in ['created_at', 'updated_at']:
            if result.get(field_name) and isinstance(result[field_name], datetime):
                result[field_name] = result[field_name].isoformat()
        return result


@dataclass
class ClassificationResult:
    """
    Result from AI classification service.
    Contains department assignment and confidence metrics.
    """
    department: DepartmentType
    assigned_to: str
    confidence_score: float
    reasoning: str
    processing_time_ms: int
    model_version: str = "gemini-1.5-pro"

    def is_confident(self, threshold: float = 0.8) -> bool:
        """Check if classification confidence meets threshold"""
        return self.confidence_score >= threshold


@dataclass
class RoutingResult:
    """
    Result from ticket routing operation.
    Contains success/failure status and external system response.
    """
    success: bool
    system_name: str
    external_ticket_id: Optional[str] = None
    response_data: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
    http_status_code: Optional[int] = None
    processing_time_ms: int = 0


# Pydantic models for API validation and OpenAPI spec generation
class TicketCreateRequest(BaseModel):
    """API request model for creating tickets"""
    title: str = Field(..., min_length=1, max_length=200, description="Ticket title")
    description: str = Field(..., min_length=1, max_length=5000, description="Detailed description")
    email: EmailStr = Field(..., description="Reporter email address")
    priority: TicketPriority = Field(default=TicketPriority.MEDIUM, description="Ticket priority")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class TicketResponse(BaseModel):
    """API response model for ticket operations"""
    ticket_id: str
    status: TicketStatus
    department: Optional[DepartmentType] = None
    assigned_to: Optional[str] = None
    external_ticket_id: Optional[str] = None
    message: str
    processing_time_ms: int


class HealthCheckResponse(BaseModel):
    """Health check endpoint response"""
    status: str
    timestamp: datetime
    version: str = "1.0.0"
    dependencies: Dict[str, str]


class MetricsResponse(BaseModel):
    """Metrics endpoint response"""
    total_tickets_processed: int
    success_rate: float
    average_processing_time_ms: float
    department_distribution: Dict[str, int]
    error_rate_by_type: Dict[str, float]