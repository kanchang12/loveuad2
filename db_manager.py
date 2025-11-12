import psycopg2
from psycopg2.extras import RealDictCursor
import logging
import os

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self):
        self.conn = None
        self.connect()  # Auto-connect on initialization
    
    def connect(self):
        """Connect to database only when needed"""
        if self.conn is not None:
            return self.conn
            
        try:
            database_url = os.getenv('DATABASE_URL')
            if database_url:
                self.conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
            else:
                raise Exception("DATABASE_URL not set")
            logger.info("Database connected successfully")
            return self.conn
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            raise
    
    def get_patient_data(self, code_hash):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM patients WHERE code_hash = %s;", (code_hash,))
                return cur.fetchone()
        except Exception as e:
            logger.error(f"Error fetching patient data: {e}")
            conn.rollback()
            raise
    
    def insert_patient_data(self, code_hash, encrypted_data):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO patients (code_hash, encrypted_data)
                    VALUES (%s, %s);
                """, (code_hash, encrypted_data))
                conn.commit()
        except Exception as e:
            logger.error(f"Error inserting patient data: {e}")
            conn.rollback()
            raise
    
    def get_medications(self, code_hash):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM medications 
                    WHERE patient_code_hash = %s AND active = TRUE;
                """, (code_hash,))
                return cur.fetchall()
        except Exception as e:
            logger.error(f"Error fetching medications: {e}")
            conn.rollback()
            raise
    
    def insert_medication(self, code_hash, encrypted_data):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO medications (patient_code_hash, encrypted_data)
                    VALUES (%s, %s);
                """, (code_hash, encrypted_data))
                conn.commit()
        except Exception as e:
            logger.error(f"Error inserting medication: {e}")
            conn.rollback()
            raise
    
    def get_health_records(self, code_hash):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM health_records 
                    WHERE patient_code_hash = %s 
                    ORDER BY created_at DESC;
                """, (code_hash,))
                return cur.fetchall()
        except Exception as e:
            logger.error(f"Error fetching health records: {e}")
            conn.rollback()
            raise
    
    def insert_health_record(self, code_hash, record_type, encrypted_metadata, record_date=None):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO health_records (patient_code_hash, record_type, encrypted_metadata, record_date)
                    VALUES (%s, %s, %s, %s);
                """, (code_hash, record_type, encrypted_metadata, record_date))
                conn.commit()
        except Exception as e:
            logger.error(f"Error inserting health record: {e}")
            conn.rollback()
            raise
    
    def get_conversations(self, code_hash):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT * FROM conversations 
                    WHERE patient_code_hash = %s 
                    ORDER BY created_at DESC;
                """, (code_hash,))
                return cur.fetchall()
        except Exception as e:
            logger.error(f"Error fetching conversations: {e}")
            conn.rollback()
            raise
    
    def insert_conversation(self, code_hash, encrypted_query, encrypted_response, sources):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO conversations (patient_code_hash, encrypted_query, encrypted_response, sources)
                    VALUES (%s, %s, %s, %s);
                """, (code_hash, encrypted_query, encrypted_response, sources))
                conn.commit()
        except Exception as e:
            logger.error(f"Error inserting conversation: {e}")
            conn.rollback()
            raise
    
    def fts_search(self, tsquery_string, top_k=5):
        """Full-Text Search using PostgreSQL tsvector"""
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        c.chunk_text,
                        p.title,
                        p.authors,
                        p.journal,
                        p.year,
                        p.doi,
                        ts_rank(c.chunk_fts, to_tsquery('english', %s)) as similarity
                    FROM paper_chunks c
                    JOIN research_papers p ON c.paper_id = p.id
                    WHERE c.chunk_fts @@ to_tsquery('english', %s)
                    ORDER BY similarity DESC
                    LIMIT %s;
                """, (tsquery_string, tsquery_string, top_k))
                
                return cur.fetchall()
        except Exception as e:
            logger.error(f"FTS search error: {e}")
            conn.rollback()
            return []
    
    def get_stats(self):
        conn = self.connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) as total_papers FROM research_papers;")
                papers = cur.fetchone()['total_papers']
                cur.execute("SELECT COUNT(*) as total_chunks FROM paper_chunks;")
                chunks = cur.fetchone()['total_chunks']
                return {'total_papers': papers, 'total_chunks': chunks}
        except Exception as e:
            logger.error(f"Error fetching stats: {e}")
            conn.rollback()
            raise
