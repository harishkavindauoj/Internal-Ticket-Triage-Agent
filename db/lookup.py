"""
Database operations for team mapping and ticket storage.
Uses SQLAlchemy with async support for production scalability.
"""

import os
from datetime import datetime
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager

from sqlalchemy import create_engine, text, Column, Integer, String, DateTime, Boolean, JSON, func
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import select, insert, update, delete

from models.ticket import TeamMapping, DepartmentType, TicketPriority, ProcessedTicket

# Database configuration
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./tickets.db")
Base = declarative_base()


class TeamMappingDB(Base):
    """SQLAlchemy model for team mapping table"""
    __tablename__ = "team_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    department = Column(String(50), nullable=False, index=True)
    team_name = Column(String(100), nullable=False)
    api_endpoint = Column(String(500), nullable=False)
    api_method = Column(String(10), default="POST")
    api_headers = Column(JSON, default=dict)
    priority_threshold = Column(String(20), default="low")
    is_active = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TicketLogDB(Base):
    """SQLAlchemy model for ticket processing log"""
    __tablename__ = "ticket_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(String(100), nullable=False, unique=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(String(5000), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    priority = Column(String(20), nullable=False)
    department = Column(String(50), index=True)
    assigned_to = Column(String(100))
    status = Column(String(20), nullable=False, index=True)
    confidence_score = Column(String(10))  # Store as string to avoid float precision issues
    external_ticket_id = Column(String(100))
    routed_to_system = Column(String(50))
    ticket_metadata = Column(JSON, default=dict)
    error_message = Column(String(1000))
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DatabaseManager:
    """
    Async database manager for ticket triage operations.
    Handles team mapping lookups and ticket logging with connection pooling.
    """

    def __init__(self, database_url: str = None):
        self.database_url = database_url or DATABASE_URL
        self.engine = create_async_engine(
            self.database_url,
            echo=False,  # Set to True for SQL debugging
            pool_pre_ping=True,  # Verify connections before use
            pool_recycle=3600,  # Recycle connections every hour
        )
        self.async_session = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

    async def initialize_database(self):
        """Create tables and insert default team mappings"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Insert default team mappings if none exist
        await self._insert_default_mappings()

    async def _insert_default_mappings(self):
        """Insert default team mappings for common departments"""
        default_mappings = [
            TeamMapping(
                department=DepartmentType.IT,
                team_name="IT Support Team",
                api_endpoint="https://your-company.atlassian.net/rest/api/2/issue",
                api_method="POST",
                api_headers={"Content-Type": "application/json", "Authorization": "Bearer YOUR_TOKEN"},
                priority_threshold=TicketPriority.LOW
            ),
            TeamMapping(
                department=DepartmentType.HR,
                team_name="HR Operations",
                api_endpoint="https://your-company.freshservice.com/api/v2/tickets",
                api_method="POST",
                api_headers={"Content-Type": "application/json", "Authorization": "Basic YOUR_TOKEN"},
                priority_threshold=TicketPriority.MEDIUM
            ),
            TeamMapping(
                department=DepartmentType.FACILITIES,
                team_name="Facilities Management",
                api_endpoint="https://webhook.site/facilities-test",
                api_method="POST",
                api_headers={"Content-Type": "application/json"},
                priority_threshold=TicketPriority.LOW
            ),
            TeamMapping(
                department=DepartmentType.SECURITY,
                team_name="InfoSec Team",
                api_endpoint="https://webhook.site/security-test",
                api_method="POST",
                api_headers={"Content-Type": "application/json"},
                priority_threshold=TicketPriority.HIGH
            )
        ]

        async with self.async_session() as session:
            try:
                # Check if any mappings exist
                result = await session.execute(select(TeamMappingDB).limit(1))
                if result.first():
                    return  # Mappings already exist

                # Insert default mappings
                for mapping in default_mappings:
                    db_mapping = TeamMappingDB(
                        department=mapping.department.value,
                        team_name=mapping.team_name,
                        api_endpoint=mapping.api_endpoint,
                        api_method=mapping.api_method,
                        api_headers=mapping.api_headers,
                        priority_threshold=mapping.priority_threshold.value,
                        is_active=mapping.is_active
                    )
                    session.add(db_mapping)

                await session.commit()
            except Exception as e:
                await session.rollback()
                raise e

    async def get_team_mapping(self, department: DepartmentType, priority: TicketPriority = TicketPriority.LOW) -> \
    Optional[TeamMapping]:
        """
        Get team mapping for a department with priority filtering.
        Returns the most appropriate team based on priority threshold.
        """
        async with self.async_session() as session:
            try:
                # Query for active mappings for the department
                query = select(TeamMappingDB).where(
                    TeamMappingDB.department == department.value,
                    TeamMappingDB.is_active == True
                ).order_by(TeamMappingDB.priority_threshold.desc())

                result = await session.execute(query)
                mappings = result.scalars().all()

                if not mappings:
                    return None

                # Find the best match based on priority
                priority_values = {"low": 1, "medium": 2, "high": 3, "critical": 4}
                ticket_priority_value = priority_values.get(priority.value, 1)

                for mapping in mappings:
                    mapping_priority_value = priority_values.get(mapping.priority_threshold, 1)
                    if ticket_priority_value >= mapping_priority_value:
                        return TeamMapping(
                            id=mapping.id,
                            department=DepartmentType(mapping.department),
                            team_name=mapping.team_name,
                            api_endpoint=mapping.api_endpoint,
                            api_method=mapping.api_method,
                            api_headers=mapping.api_headers or {},
                            priority_threshold=TicketPriority(mapping.priority_threshold),
                            is_active=mapping.is_active,
                            created_at=mapping.created_at,
                            updated_at=mapping.updated_at
                        )

                # Return the first mapping if no priority match
                mapping = mappings[0]
                return TeamMapping(
                    id=mapping.id,
                    department=DepartmentType(mapping.department),
                    team_name=mapping.team_name,
                    api_endpoint=mapping.api_endpoint,
                    api_method=mapping.api_method,
                    api_headers=mapping.api_headers or {},
                    priority_threshold=TicketPriority(mapping.priority_threshold),
                    is_active=mapping.is_active,
                    created_at=mapping.created_at,
                    updated_at=mapping.updated_at
                )

            except Exception as e:
                raise Exception(f"Database error getting team mapping: {str(e)}")

    async def get_all_team_mappings(self) -> List[TeamMapping]:
        """Get all active team mappings"""
        async with self.async_session() as session:
            try:
                query = select(TeamMappingDB).where(TeamMappingDB.is_active == True)
                result = await session.execute(query)
                mappings = result.scalars().all()

                return [
                    TeamMapping(
                        id=mapping.id,
                        department=DepartmentType(mapping.department),
                        team_name=mapping.team_name,
                        api_endpoint=mapping.api_endpoint,
                        api_method=mapping.api_method,
                        api_headers=mapping.api_headers or {},
                        priority_threshold=TicketPriority(mapping.priority_threshold),
                        is_active=mapping.is_active,
                        created_at=mapping.created_at,
                        updated_at=mapping.updated_at
                    )
                    for mapping in mappings
                ]
            except Exception as e:
                raise Exception(f"Database error getting all team mappings: {str(e)}")

    async def log_ticket(self, ticket: ProcessedTicket):
        """Log processed ticket to database for auditing and metrics"""
        async with self.async_session() as session:
            try:
                # Check if ticket already exists
                query = select(TicketLogDB).where(TicketLogDB.ticket_id == ticket.ticket_id)
                result = await session.execute(query)
                existing = result.scalar_one_or_none()

                if existing:
                    # Update existing ticket
                    await session.execute(
                        update(TicketLogDB)
                        .where(TicketLogDB.ticket_id == ticket.ticket_id)
                        .values(
                            department=ticket.department.value if ticket.department else None,
                            assigned_to=ticket.assigned_to,
                            status=ticket.status.value,
                            confidence_score=str(ticket.confidence_score) if ticket.confidence_score else None,
                            external_ticket_id=ticket.external_ticket_id,
                            routed_to_system=ticket.routed_to_system,
                            error_message=ticket.routing_error,
                            updated_at=datetime.utcnow()
                        )
                    )
                else:
                    # Insert new ticket
                    ticket_log = TicketLogDB(
                        ticket_id=ticket.ticket_id,
                        title=ticket.title,
                        description=ticket.description,
                        email=ticket.email,
                        priority=ticket.priority.value,
                        department=ticket.department.value if ticket.department else None,
                        assigned_to=ticket.assigned_to,
                        status=ticket.status.value,
                        confidence_score=str(ticket.confidence_score) if ticket.confidence_score else None,
                        external_ticket_id=ticket.external_ticket_id,
                        routed_to_system=ticket.routed_to_system,
                        metadata=ticket.metadata,
                        error_message=ticket.routing_error
                    )
                    session.add(ticket_log)

                await session.commit()

            except Exception as e:
                await session.rollback()
                raise Exception(f"Database error logging ticket: {str(e)}")

    async def get_metrics(self) -> Dict[str, Any]:
        """Get processing metrics for monitoring dashboard"""
        async with self.async_session() as session:
            try:
                # Total tickets processed - Fixed approach
                total_result = await session.execute(
                    select(func.count(TicketLogDB.id))
                )
                total_tickets = total_result.scalar() or 0

                # Success rate (tickets that were successfully routed)
                success_result = await session.execute(
                    select(func.count(TicketLogDB.id))
                    .where(TicketLogDB.status == "routed")
                )
                successful_tickets = success_result.scalar() or 0
                success_rate = (successful_tickets / total_tickets * 100) if total_tickets > 0 else 0

                # Department distribution - FIXED VERSION
                dept_result = await session.execute(
                    select(
                        TicketLogDB.department,
                        func.count(TicketLogDB.id).label('count')
                    ).group_by(TicketLogDB.department)
                )

                # Proper way to access the results
                department_distribution = {}
                for row in dept_result:
                    dept_name = row.department or "unknown"
                    count = row.count  # This now works correctly with func.count()
                    department_distribution[dept_name] = count

                return {
                    "total_tickets_processed": total_tickets,
                    "success_rate": round(success_rate, 2),
                    "department_distribution": department_distribution,
                    "last_updated": datetime.utcnow().isoformat()
                }

            except Exception as e:
                raise Exception(f"Database error getting metrics: {str(e)}")

    async def close(self):
        """Close database connections"""
        await self.engine.dispose()


# Global database manager instance
db_manager = DatabaseManager()


@asynccontextmanager
async def get_db_session():
    """Context manager for database sessions"""
    async with db_manager.async_session() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()