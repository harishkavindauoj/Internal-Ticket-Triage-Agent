"""
Ticket routing service for sending classified tickets to external systems.
Handles multiple destination systems (Jira, Freshservice, etc.) with resilient HTTP client.
"""

import os
import time
import json
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse

import httpx
from httpx import AsyncClient, Timeout, Limits

from models.ticket import (
    ProcessedTicket,
    TeamMapping,
    RoutingResult,
    DepartmentType,
    TicketPriority
)
from utils.logger import timing_decorator, retry_with_backoff, APICallLogger, TicketLogger


class TicketRouter:
    """
    Production-ready ticket routing service.

    Features:
    - Multi-system routing (Jira, Freshservice, Slack, etc.)
    - HTTP client with connection pooling and timeouts
    - Automatic retry with exponential backoff
    - Request/response transformation
    - Comprehensive error handling and logging
    - Rate limiting and circuit breaker patterns
    """

    def __init__(self):
        # HTTP client configuration for production use
        self.timeout = Timeout(
            connect=10.0,  # Connection timeout
            read=30.0,  # Read timeout
            write=10.0,  # Write timeout
            pool=5.0  # Pool timeout
        )

        self.limits = Limits(
            max_keepalive_connections=20,
            max_connections=100,
            keepalive_expiry=30.0
        )

        # Initialize async HTTP client
        self.client = AsyncClient(
            timeout=self.timeout,
            limits=self.limits,
            follow_redirects=True,
            verify=True  # SSL verification enabled
        )

        # System-specific configurations
        self.system_configs = {
            "jira": {
                "base_url": os.getenv("JIRA_API_URL", "https://your-company.atlassian.net/rest/api/2"),
                "auth_header": f"Bearer {os.getenv('JIRA_TOKEN', 'your_token')}",
                "content_type": "application/json"
            },
            "freshservice": {
                "base_url": os.getenv("FRESHSERVICE_API_URL", "https://your-company.freshservice.com/api/v2"),
                "auth_header": f"Basic {os.getenv('FRESHSERVICE_TOKEN', 'your_token')}",
                "content_type": "application/json"
            },
            "slack": {
                "webhook_url": os.getenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK"),
                "content_type": "application/json"
            }
        }

        # Circuit breaker state tracking
        self.circuit_breaker_state = {}
        self.failure_counts = {}
        self.last_failure_time = {}

    def _get_system_from_endpoint(self, endpoint: str) -> str:
        """Identify target system from endpoint URL"""
        parsed = urlparse(endpoint)
        domain = parsed.netloc.lower()

        if "atlassian.net" in domain or "jira" in domain:
            return "jira"
        elif "freshservice" in domain:
            return "freshservice"
        elif "slack.com" in domain:
            return "slack"
        elif "webhook.site" in domain:
            return "webhook_test"
        else:
            return "unknown"

    def _transform_ticket_for_jira(self, ticket: ProcessedTicket) -> Dict[str, Any]:
        """Transform ticket data for Jira API format"""
        priority_mapping = {
            TicketPriority.LOW: "Low",
            TicketPriority.MEDIUM: "Medium",
            TicketPriority.HIGH: "High",
            TicketPriority.CRITICAL: "Highest"
        }

        return {
            "fields": {
                "project": {"key": "SUPP"},  # Default project key
                "summary": ticket.title,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {
                                    "type": "text",
                                    "text": ticket.description
                                }
                            ]
                        }
                    ]
                },
                "issuetype": {"name": "Task"},
                "priority": {"name": priority_mapping.get(ticket.priority, "Medium")},
                "reporter": {"emailAddress": ticket.email},
                "labels": [
                    f"department:{ticket.department.value.lower()}" if ticket.department else "department:general",
                    f"auto-routed",
                    f"confidence:{int(ticket.confidence_score * 100)}" if ticket.confidence_score else "confidence:unknown"
                ],
                "customfield_10001": ticket.assigned_to  # Custom field for assigned team
            }
        }

    def _transform_ticket_for_freshservice(self, ticket: ProcessedTicket) -> Dict[str, Any]:
        """Transform ticket data for Freshservice API format"""
        priority_mapping = {
            TicketPriority.LOW: 1,
            TicketPriority.MEDIUM: 2,
            TicketPriority.HIGH: 3,
            TicketPriority.CRITICAL: 4
        }

        return {
            "ticket": {
                "subject": ticket.title,
                "description": ticket.description,
                "email": ticket.email,
                "priority": priority_mapping.get(ticket.priority, 2),
                "status": 2,  # Open status
                "source": 2,  # Email source
                "tags": [
                    f"department:{ticket.department.value.lower()}" if ticket.department else "department:general",
                    "auto-routed",
                    ticket.assigned_to
                ],
                "custom_fields": {
                    "assigned_team": ticket.assigned_to,
                    "ai_confidence": ticket.confidence_score if ticket.confidence_score else 0.0,
                    "classification_reasoning": ticket.classification_reasoning or ""
                }
            }
        }

    def _transform_ticket_for_slack(self, ticket: ProcessedTicket) -> Dict[str, Any]:
        """Transform ticket data for Slack webhook format"""
        color_mapping = {
            TicketPriority.LOW: "#36a64f",  # Green
            TicketPriority.MEDIUM: "#ff9500",  # Orange
            TicketPriority.HIGH: "#ff0000",  # Red
            TicketPriority.CRITICAL: "#800080"  # Purple
        }

        return {
            "text": f"New Ticket: {ticket.title}",
            "attachments": [
                {
                    "color": color_mapping.get(ticket.priority, "#36a64f"),
                    "fields": [
                        {
                            "title": "Title",
                            "value": ticket.title,
                            "short": False
                        },
                        {
                            "title": "Description",
                            "value": ticket.description[:300] + "..." if len(
                                ticket.description) > 300 else ticket.description,
                            "short": False
                        },
                        {
                            "title": "Reporter",
                            "value": ticket.email,
                            "short": True
                        },
                        {
                            "title": "Priority",
                            "value": ticket.priority.value.upper(),
                            "short": True
                        },
                        {
                            "title": "Department",
                            "value": ticket.department.value if ticket.department else "GENERAL",
                            "short": True
                        },
                        {
                            "title": "Assigned To",
                            "value": ticket.assigned_to or "Unassigned",
                            "short": True
                        }
                    ],
                    "footer": f"Ticket ID: {ticket.ticket_id}",
                    "ts": int(ticket.created_at.timestamp()) if ticket.created_at else int(time.time())
                }
            ]
        }

    def _transform_ticket_payload(self, ticket: ProcessedTicket, system: str) -> Dict[str, Any]:
        """Transform ticket to appropriate format based on target system"""
        transformers = {
            "jira": self._transform_ticket_for_jira,
            "freshservice": self._transform_ticket_for_freshservice,
            "slack": self._transform_ticket_for_slack,
            "webhook_test": lambda t: t.to_dict(),  # Generic format for testing
            "unknown": lambda t: t.to_dict()  # Fallback format
        }

        transformer = transformers.get(system, transformers["unknown"])
        return transformer(ticket)

    def _is_circuit_breaker_open(self, endpoint: str) -> bool:
        """Check if circuit breaker is open for endpoint"""
        if endpoint not in self.circuit_breaker_state:
            return False

        # Circuit breaker opens after 5 failures
        failure_count = self.failure_counts.get(endpoint, 0)
        if failure_count < 5:
            return False

        # Reset circuit breaker after 5 minutes
        last_failure = self.last_failure_time.get(endpoint, 0)
        if time.time() - last_failure > 300:  # 5 minutes
            self.circuit_breaker_state[endpoint] = False
            self.failure_counts[endpoint] = 0
            return False

        return self.circuit_breaker_state.get(endpoint, False)

    def _record_failure(self, endpoint: str):
        """Record failure for circuit breaker logic"""
        self.failure_counts[endpoint] = self.failure_counts.get(endpoint, 0) + 1
        self.last_failure_time[endpoint] = time.time()

        if self.failure_counts[endpoint] >= 5:
            self.circuit_breaker_state[endpoint] = True

    def _record_success(self, endpoint: str):
        """Record success and reset circuit breaker state"""
        self.failure_counts[endpoint] = 0
        self.circuit_breaker_state[endpoint] = False

    def _prepare_headers(self, team_mapping: TeamMapping, system: str) -> Dict[str, str]:
        """Prepare HTTP headers based on system configuration and team mapping"""
        headers = {"User-Agent": "TicketTriageAgent/1.0"}

        # Add system-specific headers
        system_config = self.system_configs.get(system, {})
        if "content_type" in system_config:
            headers["Content-Type"] = system_config["content_type"]

        if "auth_header" in system_config and system != "slack":
            headers["Authorization"] = system_config["auth_header"]

        # Add team mapping headers (override system defaults)
        if team_mapping.api_headers:
            headers.update(team_mapping.api_headers)

        return headers

    @timing_decorator("ticket_routing")
    @retry_with_backoff(max_attempts=3, exceptions=(httpx.RequestError, httpx.HTTPStatusError))
    async def route_ticket(self, ticket: ProcessedTicket, team_mapping: TeamMapping) -> RoutingResult:
        """
        Route ticket to external system based on team mapping.

        Args:
            ticket: Processed ticket with classification results
            team_mapping: Team configuration with API endpoint details

        Returns:
            RoutingResult with success status and external ticket ID
        """
        ticket_logger = TicketLogger(ticket.ticket_id)
        start_time = time.time()

        try:
            # Check circuit breaker
            if self._is_circuit_breaker_open(team_mapping.api_endpoint):
                error_msg = f"Circuit breaker open for {team_mapping.api_endpoint}"
                ticket_logger.error(error_msg)

                return RoutingResult(
                    success=False,
                    system_name="circuit_breaker",
                    error_message=error_msg,
                    processing_time_ms=int((time.time() - start_time) * 1000)
                )

            # Identify target system and prepare payload
            system = self._get_system_from_endpoint(team_mapping.api_endpoint)
            payload = self._transform_ticket_payload(ticket, system)
            headers = self._prepare_headers(team_mapping, system)

            # Initialize API call logger
            api_logger = APICallLogger(team_mapping.api_endpoint, team_mapping.api_method)

            ticket_logger.info(
                "Routing ticket to external system",
                system=system,
                endpoint=team_mapping.api_endpoint,
                method=team_mapping.api_method
            )

            # Log outgoing request
            payload_json = json.dumps(payload)
            api_logger.log_request(payload_size=len(payload_json.encode('utf-8')))

            # Make HTTP request
            response = await self.client.request(
                method=team_mapping.api_method,
                url=team_mapping.api_endpoint,
                json=payload,
                headers=headers
            )

            # Log response
            api_logger.log_response(
                status_code=response.status_code,
                response_size=len(response.content) if response.content else 0
            )

            # Check response status
            response.raise_for_status()

            # Parse response for external ticket ID
            external_ticket_id = self._extract_ticket_id(response, system)
            processing_time = int((time.time() - start_time) * 1000)

            # Record success
            self._record_success(team_mapping.api_endpoint)

            ticket_logger.log_routing_success(
                system=system,
                external_id=external_ticket_id or "unknown",
                duration_ms=processing_time
            )

            return RoutingResult(
                success=True,
                system_name=system,
                external_ticket_id=external_ticket_id,
                response_data=response.json() if response.content else {},
                http_status_code=response.status_code,
                processing_time_ms=processing_time
            )

        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP error {e.response.status_code}: {e.response.text}"
            self._record_failure(team_mapping.api_endpoint)

            processing_time = int((time.time() - start_time) * 1000)
            ticket_logger.log_routing_failure(system or "unknown", error_msg, processing_time)

            return RoutingResult(
                success=False,
                system_name=system or "unknown",
                error_message=error_msg,
                http_status_code=e.response.status_code,
                processing_time_ms=processing_time
            )

        except httpx.RequestError as e:
            error_msg = f"Request error: {str(e)}"
            self._record_failure(team_mapping.api_endpoint)

            processing_time = int((time.time() - start_time) * 1000)
            ticket_logger.log_routing_failure(system or "unknown", error_msg, processing_time)

            return RoutingResult(
                success=False,
                system_name=system or "unknown",
                error_message=error_msg,
                processing_time_ms=processing_time
            )

        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            self._record_failure(team_mapping.api_endpoint)

            processing_time = int((time.time() - start_time) * 1000)
            ticket_logger.log_routing_failure(system or "unknown", error_msg, processing_time)

            return RoutingResult(
                success=False,
                system_name=system or "unknown",
                error_message=error_msg,
                processing_time_ms=processing_time
            )

    def _extract_ticket_id(self, response: httpx.Response, system: str) -> Optional[str]:
        """Extract external ticket ID from API response"""
        try:
            if not response.content:
                return None

            response_data = response.json()

            # System-specific ID extraction
            if system == "jira":
                return response_data.get("key") or response_data.get("id")
            elif system == "freshservice":
                ticket_data = response_data.get("ticket", {})
                return str(ticket_data.get("id")) if ticket_data.get("id") else None
            elif system == "slack":
                # Slack webhooks don't return ticket IDs
                return f"slack_{int(time.time())}"
            else:
                # Generic extraction attempts
                for key in ["id", "ticket_id", "key", "number"]:
                    if key in response_data:
                        return str(response_data[key])

                # Look in nested objects
                for nested_key in ["ticket", "issue", "data"]:
                    nested_data = response_data.get(nested_key, {})
                    if isinstance(nested_data, dict):
                        for key in ["id", "ticket_id", "key", "number"]:
                            if key in nested_data:
                                return str(nested_data[key])

            return None

        except (json.JSONDecodeError, AttributeError):
            return None

    async def test_endpoint(self, endpoint: str, method: str = "GET") -> Dict[str, Any]:
        """Test connectivity to an endpoint"""
        try:
            response = await self.client.request(
                method=method,
                url=endpoint,
                timeout=Timeout(connect=5.0, read=10.0)
            )

            return {
                "success": True,
                "status_code": response.status_code,
                "response_time_ms": response.elapsed.total_seconds() * 1000,
                "endpoint": endpoint
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "endpoint": endpoint
            }

    def get_routing_stats(self) -> Dict[str, Any]:
        """Get routing service statistics"""
        return {
            "circuit_breaker_state": self.circuit_breaker_state,
            "failure_counts": self.failure_counts,
            "supported_systems": list(self.system_configs.keys()),
            "client_stats": {
                "max_connections": self.limits.max_connections,
                "max_keepalive_connections": self.limits.max_keepalive_connections,
                "timeout_config": {
                    "connect": self.timeout.connect,
                    "read": self.timeout.read,
                    "write": self.timeout.write
                }
            }
        }

    async def close(self):
        """Close HTTP client and cleanup resources"""
        await self.client.aclose()