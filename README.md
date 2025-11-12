# loveUAD API - Privacy-First Dementia Care Support

AI-powered dementia caregiver support system with research-backed guidance. Built on Google Cloud stack for the Cloud Run Hackathon.

## Features

### Privacy-First Architecture
- 17-digit anonymous patient codes
- End-to-end encryption of all user data
- Code hashing for database lookups
- PII filtering on prescription scans
- Zero identity collection

### Core Functionality
- Patient registration and login with anonymous codes
- Medication tracking and reminders
- Prescription scanning with Gemini Vision
- Health records management
- Caregiver connections
- RAG-powered dementia guidance with research citations

### Technology Stack
- Flask REST API
- Cloud SQL PostgreSQL with pgvector
- Vertex AI (Embeddings + Gemini Flash)
- Cloud Run deployment
- 16,000+ dementia research papers

## Setup Instructions

### Prerequisites

1. Google Cloud Project with billing enabled
2. APIs enabled:
   - Cloud SQL Admin API
   - Vertex AI API
   - Cloud Run API
   - Cloud Build API

3. Tools installed:
   - Python 3.11+
   - gcloud CLI
   - Cloud SQL Proxy (for local development)

### 1. Clone Repository

```bash
git clone <your-repo-url>
cd loveuad-api
```

### 2. Environment Configuration

```bash
# Copy environment template
cp .env.example .env

# Generate encryption key
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Edit .env with your values
nano .env
```

Required environment variables:
```
GCP_PROJECT_ID=your-project-id
GCP_LOCATION=europe-west2
DB_USER=postgres
DB_PASSWORD=your-secure-password
DB_NAME=loveuad
INSTANCE_CONNECTION_NAME=project:region:instance
ENCRYPTION_KEY=your-generated-key
```

### 3. Create Cloud SQL Instance

```bash
# Create Cloud SQL instance
gcloud sql instances create loveuad-db \
    --database-version=POSTGRES_15 \
    --tier=db-custom-2-7680 \
    --region=europe-west2 \
    --root-password=your-secure-password \
    --storage-size=20GB

# Create database
gcloud sql databases create loveuad --instance=loveuad-db

# Get connection name
gcloud sql instances describe loveuad-db --format='value(connectionName)'
```

### 4. Local Development Setup

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start Cloud SQL Proxy
cloud_sql_proxy -instances=<INSTANCE_CONNECTION_NAME>=tcp:5432 &

# Initialize database
python scripts/setup_db.py

# Run application
python app.py
```

### 5. Load Research Papers

```bash
# Ingest research papers from JSON
python scripts/ingest_research.py /path/to/research_papers.json

# This will:
# - Parse 16,000+ papers
# - Chunk into 1000-token pieces
# - Generate embeddings via Vertex AI
# - Store in Cloud SQL with pgvector
# - Takes approximately 2-3 hours
```

### 6. Deploy to Cloud Run

```bash
# Build and deploy
gcloud run deploy loveuad-api \
    --source . \
    --region europe-west2 \
    --platform managed \
    --allow-unauthenticated \
    --set-env-vars GCP_PROJECT_ID=your-project \
    --set-env-vars GCP_LOCATION=europe-west2 \
    --set-env-vars ENCRYPTION_KEY=your-key \
    --add-cloudsql-instances your-project:europe-west2:loveuad-db \
    --set-secrets DB_PASSWORD=db-password:latest

# Get service URL
gcloud run services describe loveuad-api --region europe-west2 --format='value(status.url)'
```

## API Endpoints

### Patient Management
- `POST /api/patient/register` - Register new patient
- `POST /api/patient/login` - Login with 17-digit code
- `GET /api/patient/qr/<code>` - Generate QR code

### Medication Management
- `POST /api/medications/add` - Add medication
- `GET /api/medications/<code_hash>` - Get medications
- `POST /api/medications/update` - Update medication
- `POST /api/medications/delete` - Delete medication

### Prescription Scanning
- `POST /api/scan/prescription` - Scan prescription with Gemini Vision

### Health Records
- `GET /api/health/records/<code_hash>` - Get health records

### Caregiver
- `POST /api/caregiver/connect` - Connect caregiver to patient

### Dementia Guidance (RAG)
- `POST /api/dementia/query` - Ask dementia question, get research-backed answer
- `GET /api/dementia/history/<code_hash>` - Get conversation history
- `GET /api/dementia/stats` - Get RAG database statistics

### System
- `GET /api/health` - Health check

## API Usage Examples

### Register Patient

```bash
curl -X POST https://your-service-url/api/patient/register \
  -H "Content-Type: application/json" \
  -d '{
    "firstName": "John",
    "lastName": "Doe",
    "age": 75,
    "gender": "Male"
  }'

# Response:
# {
#   "success": true,
#   "patientCode": "ABCD-EFGH-IJKL-MNOP-Q",
#   "codeHash": "hash..."
# }
```

### Ask Dementia Question

```bash
curl -X POST https://your-service-url/api/dementia/query \
  -H "Content-Type: application/json" \
  -d '{
    "codeHash": "patient-code-hash",
    "query": "How do I handle medication refusal in dementia patients?"
  }'

# Response:
# {
#   "success": true,
#   "answer": "Cognitive behavioral strategies have shown effectiveness... [Smith et al., 2023, Journal of Alzheimer's Disease]",
#   "sources": [
#     {
#       "title": "Medication Adherence in Dementia",
#       "authors": "Smith et al.",
#       "journal": "Journal of Alzheimer's Disease",
#       "year": 2023,
#       "doi": "10.1234/..."
#     }
#   ],
#   "disclaimer": "..."
# }
```

## Cost Estimate

Monthly costs for pilot phase (5000 queries):
- Cloud SQL (db-f1-micro): ~$7
- Storage (10GB): ~$2
- Vertex AI Embeddings: ~$1
- Gemini Flash API: ~$8
- Cloud Run: ~$2
- Cloud Storage: ~$0.50
**Total: ~$20-22/month**

**Performance Note:** db-f1-micro has 0.6GB RAM with query latency of 200-300ms. This is acceptable for pilot phase. For production with higher load, consider upgrading to db-custom-1-3840 (~$50/month) for 100ms queries.

## Security Features

1. **No Personal Data Collection**
   - Only 17-digit anonymous codes
   - No names, emails, or accounts

2. **End-to-End Encryption**
   - All user data encrypted with Fernet
   - Encryption keys managed securely

3. **PII Filtering**
   - Automatic removal of names, DOB, addresses from scans
   - Privacy-preserving OCR

4. **Code Hashing**
   - Patient codes hashed before database storage
   - SHA-256 hashing

5. **Data Isolation**
   - User data and research papers in separate schemas
   - Clear separation of concerns

## Development

### Run Tests
```bash
pytest tests/
```

### Format Code
```bash
black .
flake8 .
```

### Database Migrations
```bash
# Add new migrations in scripts/migrations/
python scripts/migrate.py
```

## Troubleshooting

### Database Connection Issues
```bash
# Check Cloud SQL Proxy is running
ps aux | grep cloud_sql_proxy

# Test database connection
psql "host=localhost port=5432 dbname=loveuad user=postgres"
```

### Vertex AI Errors
```bash
# Ensure APIs are enabled
gcloud services enable aiplatform.googleapis.com

# Check authentication
gcloud auth application-default login
```

### Embedding Generation Fails
- Check Vertex AI quotas in Cloud Console
- Verify project has billing enabled
- Ensure LOCATION is set correctly

## Support

For issues or questions:
- Email: kanchan.g12@gmail.com
- Company: LOVEUAD LTD (16838046)
- Location: Leeds, UK

## License

Proprietary - LOVEUAD LTD
