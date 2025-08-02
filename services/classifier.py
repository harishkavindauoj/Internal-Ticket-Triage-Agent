"""
AI-powered ticket classification service using Google Gemini.
Implements few-shot learning with production-ready error handling and caching.
"""

import os
import time
import json
from typing import Optional, Dict, Any, List
from dataclasses import asdict

import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

from models.ticket import (
    IncomingTicket,
    ClassificationResult,
    DepartmentType,
    TicketPriority
)
from utils.logger import timing_decorator, retry_with_backoff, TicketLogger
from dotenv import load_dotenv

load_dotenv()


class TicketClassifier:
    """
    Production-ready ticket classification service using Google Gemini.

    Features:
    - Few-shot learning with domain-specific examples
    - Confidence scoring and fallback logic
    - Request/response validation and sanitization
    - Comprehensive error handling and retry logic
    - Performance monitoring and caching
    """

    def __init__(self, api_key: str = None, model_name: str = "gemini-1.5-pro"):
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        self.model_name = model_name

        if not self.api_key:
            raise ValueError("Google API key is required for classification service")

        # Configure Gemini client
        genai.configure(api_key=self.api_key)

        # Initialize model with safety settings
        self.model = genai.GenerativeModel(
            model_name=self.model_name,
            safety_settings={
                HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
            }
        )

        # Classification cache for similar tickets (in-memory for demo)
        self._classification_cache: Dict[str, ClassificationResult] = {}

        # Department-specific team mappings
        self.team_mappings = {
            DepartmentType.IT: [
                "it_support_team", "network_team", "security_team", "infrastructure_team"
            ],
            DepartmentType.HR: [
                "hr_operations", "recruiting_team", "benefits_team", "employee_relations"
            ],
            DepartmentType.FACILITIES: [
                "facilities_management", "maintenance_team", "office_services"
            ],
            DepartmentType.FINANCE: [
                "finance_team", "accounting_team", "procurement_team"
            ],
            DepartmentType.LEGAL: [
                "legal_team", "compliance_team", "contracts_team"
            ],
            DepartmentType.SECURITY: [
                "physical_security", "infosec_team", "compliance_security"
            ],
            DepartmentType.GENERAL: [
                "general_support", "admin_team"
            ]
        }

    def _build_classification_prompt(self, ticket: IncomingTicket) -> str:
        """
        Build few-shot learning prompt with domain-specific examples.
        Uses structured examples to improve classification accuracy.
        """
        examples = [
            {
                "title": "VPN connection issues after Windows update",
                "description": "Cannot connect to company VPN after latest Windows update. Getting timeout errors.",
                "department": "IT",
                "team": "network_team",
                "reasoning": "Network connectivity issue requiring IT support for VPN troubleshooting."
            },
            {
                "title": "New employee onboarding documents",
                "description": "Need to complete I-9 forms and benefits enrollment for new hire starting Monday.",
                "department": "HR",
                "team": "hr_operations",
                "reasoning": "Employee onboarding and documentation handled by HR operations."
            },
            {
                "title": "Conference room booking system not working",
                "description": "Meeting room reservation system is down, cannot book rooms for client meetings.",
                "department": "FACILITIES",
                "team": "facilities_management",
                "reasoning": "Office systems and meeting room management falls under facilities."
            },
            {
                "title": "Expense report approval delayed",
                "description": "Submitted expense report 2 weeks ago but still pending approval in the system.",
                "department": "FINANCE",
                "team": "finance_team",
                "reasoning": "Expense management and approval processes are handled by finance."
            },
            {
                "title": "Data privacy compliance question",
                "description": "Need guidance on GDPR compliance for customer data collection in new product.",
                "department": "LEGAL",
                "team": "compliance_team",
                "reasoning": "Privacy compliance and legal guidance required from legal team."
            },
            {
                "title": "Suspicious email with potential malware",
                "description": "Received suspicious email with attachment, may be phishing attempt.",
                "department": "SECURITY",
                "team": "infosec_team",
                "reasoning": "Security incident requiring immediate attention from information security."
            }
        ]

        # Build the prompt with examples and ticket data
        prompt = """You are an expert IT ticket classifier for a corporate environment. 

Your task is to classify incoming tickets into the appropriate department and assign them to the most suitable team.

Available departments: IT, HR, FACILITIES, FINANCE, LEGAL, SECURITY, GENERAL

Here are examples of correct classifications:

"""

        # Add examples
        for i, example in enumerate(examples, 1):
            prompt += f"""Example {i}:
Title: {example['title']}
Description: {example['description']}
Classification:
- Department: {example['department']}
- Team: {example['team']}
- Reasoning: {example['reasoning']}

"""

        # Add the ticket to classify
        prompt += f"""Now classify this ticket:

Title: {ticket.title}
Description: {ticket.description}
Priority: {ticket.priority.value}
User Email: {ticket.email}

Provide your classification in this exact JSON format:
{{
    "department": "DEPARTMENT_NAME",
    "team": "specific_team_name",
    "confidence": 0.95,
    "reasoning": "Brief explanation of why this classification was chosen"
}}

Important guidelines:
1. Be conservative with confidence scores (0.6-1.0 range)
2. Use GENERAL department only when no other department clearly fits
3. Consider the priority level when assigning teams
4. Base your decision on keywords, context, and business domain knowledge
5. Provide clear reasoning for your classification choice

Classification:"""

        return prompt

    def _generate_cache_key(self, ticket: IncomingTicket) -> str:
        """Generate cache key for ticket classification"""
        # Use title and description for similarity matching
        content = f"{ticket.title.lower()} {ticket.description.lower()}"
        # Simple hash for demo - in production, use semantic similarity
        return str(hash(content))

    def _parse_classification_response(self, response_text: str, ticket_logger: TicketLogger) -> Optional[
        Dict[str, Any]]:
        """
        Parse and validate Gemini's classification response.
        Handles various response formats and validates against expected schema.
        """
        try:
            # Extract JSON from response (handle various formats)
            response_text = response_text.strip()

            # Find JSON content between curly braces
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}') + 1

            if start_idx == -1 or end_idx == 0:
                ticket_logger.warning("No JSON found in classification response", response=response_text[:200])
                return None

            json_content = response_text[start_idx:end_idx]
            result = json.loads(json_content)

            # Validate required fields
            required_fields = ['department', 'team', 'confidence', 'reasoning']
            for field in required_fields:
                if field not in result:
                    ticket_logger.warning(f"Missing required field in classification: {field}")
                    return None

            # Validate department
            try:
                dept = DepartmentType(result['department'])
                result['department'] = dept
            except ValueError:
                ticket_logger.warning(f"Invalid department in classification: {result['department']}")
                # Default to GENERAL for invalid departments
                result['department'] = DepartmentType.GENERAL

            # Validate confidence score
            confidence = float(result['confidence'])
            if not 0.0 <= confidence <= 1.0:
                ticket_logger.warning(f"Invalid confidence score: {confidence}")
                confidence = max(0.0, min(1.0, confidence))  # Clamp to valid range
            result['confidence'] = confidence

            # Validate team assignment
            if result['department'] in self.team_mappings:
                available_teams = self.team_mappings[result['department']]
                if result['team'] not in available_teams:
                    # Assign default team for department
                    result['team'] = available_teams[0]
                    ticket_logger.info(f"Assigned default team for department: {result['team']}")

            return result

        except json.JSONDecodeError as e:
            ticket_logger.error("Failed to parse JSON from classification response", error=e)
            return None
        except Exception as e:
            ticket_logger.error("Unexpected error parsing classification response", error=e)
            return None

    def _create_fallback_classification(self, ticket: IncomingTicket) -> ClassificationResult:
        """
        Create fallback classification when AI classification fails.
        Uses keyword-based rules as backup classification method.
        """
        title_lower = ticket.title.lower()
        desc_lower = ticket.description.lower()
        combined_text = f"{title_lower} {desc_lower}"

        # Keyword-based classification rules
        it_keywords = ['vpn', 'computer', 'laptop', 'password', 'email', 'network', 'wifi', 'software', 'login',
                       'system']
        hr_keywords = ['benefits', 'payroll', 'vacation', 'pto', 'onboarding', 'training', 'employment', 'hiring']
        facilities_keywords = ['office', 'room', 'building', 'parking', 'heating', 'cooling', 'maintenance', 'cleaning']
        security_keywords = ['phishing', 'malware', 'suspicious', 'breach', 'access', 'badge', 'security']
        finance_keywords = ['expense', 'invoice', 'payment', 'budget', 'procurement', 'vendor', 'reimbursement']
        legal_keywords = ['contract', 'compliance', 'gdpr', 'privacy', 'legal', 'lawsuit', 'regulation']

        # Score each department based on keyword matches
        scores = {
            DepartmentType.IT: sum(1 for kw in it_keywords if kw in combined_text),
            DepartmentType.HR: sum(1 for kw in hr_keywords if kw in combined_text),
            DepartmentType.FACILITIES: sum(1 for kw in facilities_keywords if kw in combined_text),
            DepartmentType.SECURITY: sum(1 for kw in security_keywords if kw in combined_text),
            DepartmentType.FINANCE: sum(1 for kw in finance_keywords if kw in combined_text),
            DepartmentType.LEGAL: sum(1 for kw in legal_keywords if kw in combined_text),
        }

        # Find department with highest score
        best_department = max(scores.keys(), key=lambda k: scores[k])
        best_score = scores[best_department]

        # If no keywords match, default to GENERAL
        if best_score == 0:
            best_department = DepartmentType.GENERAL
            confidence = 0.3
        else:
            # Calculate confidence based on keyword matches
            confidence = min(0.7, 0.4 + (best_score * 0.1))

        # Select appropriate team
        available_teams = self.team_mappings[best_department]
        assigned_team = available_teams[0]  # Default to first team

        return ClassificationResult(
            department=best_department,
            assigned_to=assigned_team,
            confidence_score=confidence,
            reasoning=f"Fallback classification based on keyword analysis. Matched {best_score} keywords for {best_department.value}.",
            processing_time_ms=0,
            model_version="fallback-keywords"
        )

    @timing_decorator("ticket_classification")
    @retry_with_backoff(max_attempts=3, exceptions=(Exception,))
    async def classify_ticket(self, ticket: IncomingTicket) -> ClassificationResult:
        """
        Classify ticket using Gemini AI with fallback logic.

        Args:
            ticket: The incoming ticket to classify

        Returns:
            ClassificationResult with department, team, and confidence

        Raises:
            Exception: When all classification attempts fail
        """
        ticket_logger = TicketLogger(f"classify_{hash(ticket.title)}")
        start_time = time.time()

        try:
            # Check cache first
            cache_key = self._generate_cache_key(ticket)
            if cache_key in self._classification_cache:
                cached_result = self._classification_cache[cache_key]
                ticket_logger.info("Using cached classification", department=cached_result.department.value)
                return cached_result

            # Build classification prompt
            prompt = self._build_classification_prompt(ticket)

            ticket_logger.info("Sending classification request to Gemini")

            # Generate classification using Gemini
            response = self.model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.1,  # Low temperature for consistent results
                    top_p=0.9,
                    top_k=40,
                    max_output_tokens=500,
                )
            )

            if not response.text:
                raise Exception("Empty response from Gemini API")

            # Parse response
            parsed_result = self._parse_classification_response(response.text, ticket_logger)

            if not parsed_result:
                ticket_logger.warning("Failed to parse classification, using fallback")
                return self._create_fallback_classification(ticket)

            # Create classification result
            processing_time = int((time.time() - start_time) * 1000)

            result = ClassificationResult(
                department=parsed_result['department'],
                assigned_to=parsed_result['team'],
                confidence_score=parsed_result['confidence'],
                reasoning=parsed_result['reasoning'],
                processing_time_ms=processing_time,
                model_version=self.model_name
            )

            # Cache successful classification
            self._classification_cache[cache_key] = result

            ticket_logger.log_classification(
                department=result.department.value,
                confidence=result.confidence_score,
                reasoning=result.reasoning
            )

            return result

        except Exception as e:
            ticket_logger.error("Classification failed, using fallback", error=e)

            # Use fallback classification as last resort
            fallback_result = self._create_fallback_classification(ticket)
            processing_time = int((time.time() - start_time) * 1000)
            fallback_result.processing_time_ms = processing_time

            return fallback_result

    def get_classification_stats(self) -> Dict[str, Any]:
        """Get classification service statistics"""
        return {
            "model_name": self.model_name,
            "cache_size": len(self._classification_cache),
            "available_departments": [dept.value for dept in DepartmentType],
            "team_mappings": {
                dept.value: teams for dept, teams in self.team_mappings.items()
            }
        }

    def clear_cache(self):
        """Clear classification cache"""
        self._classification_cache.clear()