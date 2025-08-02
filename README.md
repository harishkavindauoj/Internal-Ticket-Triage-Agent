# 🚀 FastAPI + Prometheus + Grafana + n8n Monitoring Stack

A complete developer observability setup with intelligent ticket triage system featuring:
- ✅ FastAPI backend with AI-powered ticket classification and Prometheus metrics
- 📊 Prometheus for metrics collection and alerting
- 📈 Grafana dashboards for visualization
- 🔄 n8n for automation workflows
- 🎯 Google Gemini AI for intelligent ticket routing

---

## 🧱 Components

| Service     | Purpose                                  | Port | Credentials |
|-------------|------------------------------------------|------|-------------|
| FastAPI     | AI ticket triage API with Prometheus metrics | 8000 | - |
| Metrics     | Prometheus metrics endpoint              | 8001 | - |
| Prometheus  | Time-series DB for metrics collection    | 9090 | - |
| Grafana     | Dashboards & visualization               | 3000 | `admin` / `admin` |
| n8n         | No-code automation (webhooks, flows)     | 5678 | Web UI |

---

## 📦 Prerequisites

- Docker & Docker Compose
- Google Gemini API key (for AI classification)
- `.env` file with configurations (see below)

---

## 🛠️ Getting Started

### 1. Clone the Repo
```bash
git clone https://github.com/harishkavindauoj/Internal-Ticket-Triage-Agent.git
cd Internal-Ticket-Triage-Agent
```

### 2. Environment Setup
Create a `.env` file in the project root:

```env
# Gemini AI Configuration
GEMINI_API_KEY=your_gemini_api_key_here

# Database Configuration
DATABASE_URL=sqlite+aiosqlite:///./tickets.db

# External API Endpoints for Ticket Routing
JIRA_API_URL=https://your-company.atlassian.net/rest/api/2
FRESHSERVICE_API_URL=https://your-company.freshservice.com/api/v2
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK

# Authentication Tokens
JIRA_TOKEN=your_jira_token
FRESHSERVICE_TOKEN=your_freshservice_token

# Application Settings
LOG_LEVEL=INFO
ENVIRONMENT=production
RETRY_MAX_ATTEMPTS=3
RETRY_BACKOFF_FACTOR=2

# Monitoring Configuration
PROMETHEUS_METRICS_ENABLED=true
METRICS_PORT=8001

# Grafana Settings
GF_SECURITY_ADMIN_PASSWORD=your_secure_password
```

### 3. Launch Stack
```bash
docker-compose up --build
```

### 4. 📍 Access Services

| Tool | URL | Purpose |
|------|-----|---------|
| **FastAPI** | http://localhost:8000 | Main API & docs |
| **Metrics** | http://localhost:8001/metrics | Prometheus metrics |
| **Prometheus** | http://localhost:9090 | Metrics collection |
| **Grafana** | http://localhost:3000 | Dashboards |
| **n8n** | http://localhost:5678 | Automation workflows |

---

## 🎯 AI-Powered Ticket Triage

### Core Features

- **Intelligent Classification**: Uses Google Gemini 1.5 for department routing
- **Automated Routing**: Routes tickets to IT, HR, Finance, Facilities, Security
- **Real-time Monitoring**: Comprehensive Prometheus metrics
- **Database Integration**: SQLite/PostgreSQL for team mapping
- **Webhook Compatible**: Perfect for n8n integration

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/webhook/ticket` | Submit ticket for AI triage |
| `GET` | `/health` | Health check with dependency status |
| `GET` | `/metrics` | Prometheus metrics |
| `GET` | `/docs` | Interactive API documentation |

### Example Ticket Submission

```bash
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

**Response:**
```json
{
    "ticket_id": "TKT-5F43F526",
    "title": "Email server down",
    "status": "success",
    "department": "IT",
    "assigned_to": "it_support_team",
    "external_ticket_id": "JIRA-12345",
    "routed_to_system": "jira",
    "confidence_score": 0.95,
    "created_at": "2025-08-02T10:05:28.942349"
}
```

---

## 📊 Monitoring & Observability

### Prometheus Metrics

The system exposes comprehensive metrics at `/metrics`:

- **`total_tickets_processed`** – Total tickets processed
- **`success_rate`** – Percentage of successfully routed tickets
- **`average_processing_time_ms`** – Average processing time
- **`department_distribution`** – Tickets per department (IT, HR, etc.)
- **`error_rate_by_type`** – Error distribution:
  - `classification_errors`
  - `routing_errors`
  - `system_errors`

### Grafana Dashboards

Pre-configured dashboards include:
- **Ticket Triage Overview**: Success rates, processing times
- **Department Distribution**: Ticket routing analytics
- **System Health**: API response times, error rates
- **AI Performance**: Classification confidence scores

### Health Monitoring

```json
{
    "status": "healthy",
    "timestamp": "2025-08-02T10:12:17.319047Z",
    "version": "1.0.0",
    "dependencies": {
        "database": "healthy",
        "ai_classifier": "healthy",
        "ticket_router": "healthy"
    }
}
```

---

## 🔄 n8n Integration Examples

### 1. Email-to-Ticket Automation

```json
{
  "nodes": [
    {
      "name": "Email Trigger",
      "type": "n8n-nodes-base.emailReadImap",
      "parameters": {
        "protocol": "imap",
        "host": "imap.gmail.com"
      }
    },
    {
      "name": "Extract Ticket Data",
      "type": "n8n-nodes-base.set",
      "parameters": {
        "values": {
          "title": "={{ $json.subject }}",
          "description": "={{ $json.textPlain }}",
          "email": "={{ $json.from.value[0].address }}"
        }
      }
    },
    {
      "name": "Send to Triage",
      "type": "n8n-nodes-base.httpRequest",
      "parameters": {
        "url": "http://fastapi:8000/webhook/ticket",
        "method": "POST",
        "sendBody": true,
        "bodyContentType": "json"
      }
    }
  ]
}
```

### 2. Slack Integration Workflow

Create flows to:
- Monitor ticket processing failures
- Send department-specific notifications
- Escalate high-priority tickets
- Generate daily ticket summaries

---

## 🐳 Docker Configuration

### Dockerfile

```
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app files
COPY . .

# Expose FastAPI and metrics ports
ENV PORT=8000
ENV METRICS_PORT=8001
EXPOSE 8000 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

```

### Docker Compose Services

```yaml

services:
  fastapi:
    build: .
    container_name: ticket-triage-api
    env_file:
      - .env
    ports:
      - "8000:8000"
      - "8001:8001"
    depends_on:
      - prometheus
    networks:
      - monitoring

  prometheus:
    image: prom/prometheus:latest
    container_name: prometheus
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
    networks:
      - monitoring

  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=admin123
    volumes:
      - grafana_data:/var/lib/grafana
    depends_on:
      - prometheus
    networks:
      - monitoring

  n8n:
    image: n8nio/n8n
    container_name: n8n
    ports:
      - "5678:5678"
    environment:
      - N8N_BASIC_AUTH_ACTIVE=true
      - N8N_BASIC_AUTH_USER=admin
      - N8N_BASIC_AUTH_PASSWORD=admin123
      - N8N_HOST=n8n.local
      - N8N_PORT=5678
    volumes:
      - n8n_data:/home/node/.n8n
    networks:
      - monitoring

volumes:
  n8n_data:
  grafana_data:

networks:
  monitoring:
    driver: bridge

```

### Prometheus Configuration

```yaml
# prometheus/prometheus.yml
global:
  scrape_interval: 5s

scrape_configs:
  - job_name: 'fastapi'
    static_configs:
      - targets: ['fastapi:8001']

  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
```

---


### Development Mode

```bash
# Run with auto-reload
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Enable debug logging
export LOG_LEVEL=DEBUG
```

---

## 📈 Performance & Scaling

### Expected Performance
- **Throughput**: 100-500 tickets/minute (single instance)
- **AI Classification**: ~1-2 seconds per ticket
- **Database Operations**: <100ms average
- **API Response Time**: <500ms (95th percentile)

### Scaling Recommendations
- **Horizontal**: Multiple FastAPI workers
- **Database**: PostgreSQL with connection pooling
- **Caching**: Redis for classification results
- **Load Balancing**: nginx for production

---

## 🔒 Security & Production

### Security Checklist
- [ ] Secure API keys in environment variables
- [ ] Enable HTTPS/TLS in production
- [ ] Configure CORS policies
- [ ] Implement rate limiting
- [ ] Set up authentication for Grafana
- [ ] Secure n8n with proper authentication

### Production Deployment
```bash
# Build for production
docker-compose -f docker-compose.prod.yml up -d

# Scale FastAPI instances
docker-compose up --scale fastapi=3
```

---

## 🧹 Management Commands

### Stop Services
```bash
docker-compose down
```

### Clean Up (Remove Volumes)
```bash
docker-compose down -v
```

### View Logs
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f fastapi
```

---

## 📂 Project Structure

```
Internal-Ticket-Triage-Agent/
├── main.py                    # FastAPI application
├── routers/
│   └── webhook.py            # Webhook endpoints
├── services/
│   ├── classifier.py         # Gemini AI classification
│   └── router.py            # Ticket routing logic
├── models/
│   └── ticket.py            # Data models
├── db/
│   └── lookup.py            # Database operations
├── requirements.txt
├── prometheus.yml               # Prometheus config      
├── n8n_test_ticket_flow.json
├── docker-compose.yml
├── dockerfile
└── .env.example
```

---

## 🤝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Add tests for new functionality
4. Ensure all tests pass (`pytest`)
5. Submit a Pull Request

### Code Standards
- Follow PEP 8 guidelines
- Add type hints to functions
- Maintain test coverage >80%
- Update documentation for new features

---

## 📢 Tips & Best Practices

- **Prometheus**: Adjust scrape intervals based on your needs
- **Grafana**: Create custom dashboards for your specific metrics
- **n8n**: Use webhook testing to validate ticket flows
- **Security**: Always use secure credentials in production
- **Monitoring**: Set up AlertManager for critical system alerts
- **Database**: Regular backups for ticket history

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🎉 Acknowledgments

- Google Generative AI team for Gemini API
- FastAPI team for the excellent framework
- Prometheus & Grafana communities
- n8n team for workflow automation
- The open-source monitoring ecosystem

---

**Version**: 2.0.0  
**Last Updated**: August 2025  
**Python Version**: 3.12+  
**Status**: Production Ready ✅

**Stack**: FastAPI + Prometheus + Grafana + n8n + Google Gemini AI