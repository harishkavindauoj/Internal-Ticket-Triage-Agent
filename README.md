# Internal Ticket Triage Agent 🎯

A production-ready AI-powered ticket classification and routing system built with Python 3.12, FastAPI, and Google's Gemini LLM. This system automatically triages incoming IT/HR/service tickets and routes them to appropriate departments using intelligent classification.

## 🚀 Features

- **AI-Powered Classification**: Uses Google Gemini 1.5 for intelligent ticket categorization
- **Automated Routing**: Routes tickets to appropriate departments via REST API calls
- **Database Integration**: SQLite/PostgreSQL support for team mapping and configuration
- **Webhook Compatible**: Designed for seamless n8n integration
- **Production Ready**: Includes retry logic, monitoring, logging, and error handling
- **Modular Architecture**: Clean separation of concerns for maintainability
- **OpenAPI Spec**: Auto-generated documentation for easy integration testing
- **Prometheus Metrics**: Built-in monitoring and observability

## 📦 Project Structure

```
ticket-triage-agent/
├── main.py                     # FastAPI application entrypoint
├── routers/
│   └── webhook.py             # Webhook endpoints for ticket ingestion
├── services/
│   ├── classifier.py          # Gemini-based ticket classification
│   └── router.py             # Ticket routing to department systems
├── models/
│   └── ticket.py             # Data models and schemas
├── db/             
│   └── lookup.py          # Database operations and team mapping
├── utils/
│   └── logger.py             # Logging, retry decorators, and utilities
├── tests/
│   └── test_workflow.py      # Pytest unit and integration tests
├── requirements.txt          # Python dependencies
└── README.md               # This file
```

## 🛠️ Tech Stack

- **Framework**: FastAPI 0.104+
- **AI/ML**: Google Generative AI SDK (Gemini 1.5)
- **Database**: SQLAlchemy with SQLite/PostgreSQL support
- **HTTP Client**: httpx for async API calls
- **Monitoring**: Prometheus metrics integration
- **Testing**: pytest with async support
- **Validation**: Pydantic v2 for data validation
- **Logging**: Structured logging with retry mechanisms

## 📋 Prerequisites

- Python 3.12+
- Google Cloud account with Generative AI API access
- Gemini API key
- Docker (optional, for containerized deployment)
- PostgreSQL (optional, SQLite used by default)

## 🚀 Quick Start

### 1. Clone and Setup

```bash
git clone <repository-url>
cd ticket-triage-agent
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Environment Configuration

Create a `.env` file in the project root:

```env
# Gemini API Configuration
GEMINI_API_KEY=your_gemini_api_key_here

# Database Configuration
DATABASE_URL=sqlite+aiosqlite:///./tickets.db
# For PostgreSQL: postgresql+asyncpg://user:password@localhost/tickets

# External API Endpoints
JIRA_API_URL=https://your-company.atlassian.net/rest/api/2
FRESHSERVICE_API_URL=https://your-company.freshservice.com/api/v2
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK

# Authentication tokens for external services
JIRA_TOKEN=your_jira_token
FRESHSERVICE_TOKEN=your_freshservice_token

# Application Settings
LOG_LEVEL=INFO
ENVIRONMENT=production
RETRY_MAX_ATTEMPTS=3
RETRY_BACKOFF_FACTOR=2

# Monitoring
PROMETHEUS_METRICS_ENABLED=true
METRICS_PORT=8001
```

### 3. Database Setup

```bash
# Initialize the database
python -c "from db.lookup import init_db; init_db()"

# Or run migrations (if using Alembic)
alembic upgrade head
```

### 4. Run the Application

```bash
# Development mode with auto-reload
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Production mode
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

### 5. Test the API

```bash
# Health check
curl http://localhost:8000/health

# Submit a test ticket
curl -X POST http://localhost:8000/webhook/ticket \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Email server down",
    "description": "The main email server is not responding. Users cannot send or receive emails.",
    "email": "john.doe@company.com",
    "priority": "high",
    "metadata": {
        "department": "IT",
        "location": "Building A",
        "phone": "+1234567890"
    }
}'
```

## 🔧 API Endpoints

### Core Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/webhook/ticket` | Submit new ticket for triage |
| `GET` | `/health` | Health check endpoint |
| `GET` | `/metrics` | Prometheus metrics |
| `GET` | `/docs` | Interactive API documentation |
| `GET` | `/openapi.json` | OpenAPI specification |

### Webhook Payload Format

**Input** (`POST /webhook/ticket`):
```json
{
  "title": "VPN not working after update",
  "description": "Since the latest update I can't connect to VPN. Keeps timing out.",
  "email": "user@company.com",
  "priority": "medium",
  "source": "email"
}
```

**Output**:
```json
{
    "ticket_id": "TKT-5F43F526",
    "title": "Email server down",
    "status": "failed",
    "department": "IT",
    "assigned_to": "it_support_team",
    "external_ticket_id": null,
    "routed_to_system": "jira",
    "confidence_score": 0.95,
    "error_message": " ",
    "created_at": "2025-08-02T10:05:28.942349",
    "updated_at": "2025-08-02T10:05:28.942349"
}
```

## 🎯 Classification Logic

The system uses Google Gemini 1.5 with few-shot prompting to classify tickets into departments:

### Supported Departments
- **IT**: Technical issues, software, hardware, network
- **HR**: Personnel, benefits, policies, onboarding
- **Finance**: Expenses, budgets, procurement, invoicing
- **Facilities**: Office space, equipment, maintenance
- **Security**: Access control, compliance, incidents

### Classification Process
1. **Preprocessing**: Clean and normalize ticket content
2. **Few-shot Prompting**: Use examples to guide Gemini classification
3. **Confidence Scoring**: Evaluate classification certainty
4. **Fallback Logic**: Route uncertain tickets to general support

## 🗄️ Database Schema

### Tables

**teams** - Department and team mapping
```sql
CREATE TABLE team_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    department VARCHAR(50) NOT NULL,
    team_name VARCHAR(100) NOT NULL,
    api_endpoint VARCHAR(500) NOT NULL,
    api_method VARCHAR(10) DEFAULT 'POST',
    api_headers JSON DEFAULT '{}',
    priority_threshold VARCHAR(20) DEFAULT 'low',
    is_active BOOLEAN DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_department (department),
    INDEX idx_is_active (is_active)
);

```

**tickets** - Ticket processing history
```sql
CREATE TABLE ticket_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id VARCHAR(100) NOT NULL UNIQUE,
    title VARCHAR(200) NOT NULL,
    description VARCHAR(5000) NOT NULL,
    email VARCHAR(255) NOT NULL,
    priority VARCHAR(20) NOT NULL,
    department VARCHAR(50),
    assigned_to VARCHAR(100),
    status VARCHAR(20) NOT NULL,
    confidence_score VARCHAR(10),
    external_ticket_id VARCHAR(100),
    routed_to_system VARCHAR(50),
    ticket_metadata JSON DEFAULT '{}',
    error_message VARCHAR(1000),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ticket_id (ticket_id),
    INDEX idx_email (email),
    INDEX idx_status (status),
    INDEX idx_created_at (created_at),
    INDEX idx_department (department)
);

```

## 🔄 Integration with n8n

### Webhook Configuration

1. **Create HTTP Request Node** in n8n
2. **Set URL**: `http://your-server:8000/webhook/ticket`
3. **Method**: POST
4. **Headers**: `Content-Type: application/json`
5. **Body**: Map your ticket data to the required format

### Example n8n Workflow

```json
{
  "nodes": [
    {
      "name": "Email Trigger",
      "type": "n8n-nodes-base.emailReadImap"
    },
    {
      "name": "Extract Ticket Data",
      "type": "n8n-nodes-base.set"
    },
    {
      "name": "Send to Triage Agent",
      "type": "n8n-nodes-base.httpRequest",
      "parameters": {
        "url": "http://triage-agent:8000/webhook/ticket",
        "method": "POST"
      }
    }
  ]
}
```

## 📊 Monitoring and Observability

### Prometheus Metrics

The application exposes metrics at `/metrics`:

- `tickets_total` - Total tickets processed
- `tickets_by_department` - Tickets by department
- `classification_confidence` - Average confidence scores
- `processing_time_seconds` - Request processing time
- `external_api_calls_total` - External API call counts
- `retry_attempts_total` - Retry attempt metrics

### Logging

Structured JSON logging with the following levels:
- `INFO`: Normal operations
- `WARNING`: Retry attempts, low confidence classifications
- `ERROR`: Failed API calls, processing errors
- `DEBUG`: Detailed processing information

### Health Checks

The `/health` endpoint provides:
```json
{
  "status": "healthy",
  "timestamp": "2024-01-15T10:30:00Z",
  "version": "1.0.0",
  "dependencies": {
    "database": "connected",
    "gemini_api": "available",
    "external_apis": "reachable"
  }
}
```

## 🧪 Testing

### Run Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=. --cov-report=html

# Run specific test categories
pytest tests/test_workflow.py -v
pytest -k "test_classification" -v
```

### Test Categories

- **Unit Tests**: Individual component testing
- **Integration Tests**: End-to-end workflow testing
- **API Tests**: FastAPI endpoint testing
- **Mock Tests**: External API interaction testing

### Example Test Case

```python
async def test_ticket_classification():
    """Test ticket classification with Gemini"""
    ticket_data = {
        "title": "Password reset request",
        "description": "I forgot my password and need access",
        "email": "user@company.com"
    }
    
    response = await client.post("/webhook/ticket", json=ticket_data)
    assert response.status_code == 200
    assert response.json()["classification"]["department"] == "IT"
```

## 🐳 Docker Deployment

### Build and Run

```bash
# Build the image
docker build -t ticket-triage-agent .

# Run with docker-compose
docker-compose up -d

# Or run directly
docker run -p 8000:8000 --env-file .env ticket-triage-agent
```

### Docker Compose Services

- **app**: Main FastAPI application
- **postgres**: PostgreSQL database (optional)
- **prometheus**: Metrics collection
- **grafana**: Metrics visualization

## 🔒 Security Considerations

- **API Keys**: Store sensitive keys in environment variables
- **Input Validation**: All inputs validated with Pydantic
- **Rate Limiting**: Implement rate limiting for webhook endpoints
- **Authentication**: Add API key authentication for production
- **HTTPS**: Use TLS in production environments
- **CORS**: Configure CORS policies appropriately

## 📈 Performance Optimization

### Scaling Recommendations

- **Horizontal Scaling**: Run multiple worker processes
- **Database**: Use connection pooling for high throughput
- **Caching**: Implement Redis for classification caching
- **Async Processing**: Use Celery for background tasks
- **Load Balancing**: Use nginx or similar for load distribution

### Expected Performance

- **Throughput**: ~100-500 tickets/minute (single instance)
- **Latency**: ~1-3 seconds per ticket (including AI classification)
- **Reliability**: 99.9% uptime with proper infrastructure

## 🐛 Troubleshooting

### Common Issues

1. **Gemini API Errors**
   ```bash
   # Check API key
   export GEMINI_API_KEY=your_key_here
   # Verify quota limits in Google Cloud Console
   ```

2. **Database Connection Issues**
   ```bash
   # For SQLite
   chmod 664 tickets.db
   # For PostgreSQL
   pg_isready -h localhost -p 5432
   ```

3. **Import Errors**
   ```bash
   pip install -r requirements.txt
   python -c "import google.generativeai; print('OK')"
   ```

### Debug Mode

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG
uvicorn main:app --reload --log-level debug
```

## 📝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Code Standards

- Follow PEP 8 style guidelines
- Add type hints to all functions
- Write docstrings for all public methods
- Maintain test coverage above 80%
- Use Black for code formatting

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🤝 Support

- **Documentation**: `/docs` endpoint when running
- **Issues**: GitHub Issues
- **Discussions**: GitHub Discussions
- **Wiki**: Additional documentation and examples

## 🎉 Acknowledgments

- Google Generative AI team for Gemini API
- FastAPI team for the excellent framework
- The open-source community for various dependencies

---

**Version**: 1.0.0  
**Last Updated**: January 2024  
**Python Version**: 3.12+  
**Status**: Production Ready ✅