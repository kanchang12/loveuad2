import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Flask
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    
    # Google Cloud
    PROJECT_ID = os.getenv('GCP_PROJECT_ID')
    LOCATION = os.getenv('GCP_LOCATION', 'europe-west2')
    
    # Gemini API
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
    
    # Cloud SQL Connection
    DB_USER = os.getenv('DB_USER', 'postgres')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    DB_NAME = os.getenv('DB_NAME', 'postgres')  # Changed from 'loveuad' to 'postgres'
    INSTANCE_CONNECTION_NAME = os.getenv('INSTANCE_CONNECTION_NAME')
    
    # Connection string construction
    if os.getenv('ENVIRONMENT') == 'local':
        # Local development via Cloud SQL Proxy
        DB_CONNECTION_STRING = f"postgresql://{DB_USER}:{DB_PASSWORD}@localhost:5432/{DB_NAME}"
    else:
        # Cloud Run to Cloud SQL via Unix socket
        DB_CONNECTION_STRING = f"postgresql://{DB_USER}:{DB_PASSWORD}@/{DB_NAME}?host=/cloudsql/{INSTANCE_CONNECTION_NAME}"
    
    # Encryption
    ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY')
    
    # Vertex AI Models
    EMBEDDING_MODEL = "text-embedding-004"
    LLM_MODEL = "gemini-2.0-flash-exp"
    VISION_MODEL = "gemini-2.0-flash-exp"
    
    # RAG Parameters
    TOP_K_RESULTS = 5
    CHUNK_SIZE = 1000
    MAX_CONTEXT_TOKENS = 4000
    TEMPERATURE = 0.3
    MAX_OUTPUT_TOKENS = 1024
    
    # Data Retention
    VITALS_RETENTION_DAYS = 90
