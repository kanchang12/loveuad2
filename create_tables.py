#!/usr/bin/env python3
"""
One-time script to create analytics tables
Run once, then delete this file
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor

def create_analytics_tables():
    """Create the 3 analytics tables"""
    
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        print("❌ DATABASE_URL not set")
        return
    
    try:
        conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
        print("✓ Connected to database")
        
        with conn.cursor() as cur:
            # Drop existing tables
            cur.execute("""
                DROP TABLE IF EXISTS survey_responses CASCADE;
                DROP TABLE IF EXISTS daily_active_users CASCADE;
                DROP TABLE IF EXISTS daily_launch_tracker CASCADE;
            """)
            print("✓ Dropped old tables")
            
            # Create survey_responses
            cur.execute("""
                CREATE TABLE survey_responses (
                    id SERIAL PRIMARY KEY,
                    code_hash VARCHAR(64) NOT NULL,
                    completion_date DATE NOT NULL DEFAULT CURRENT_DATE,
                    result_bucket VARCHAR(10) NOT NULL CHECK (result_bucket IN ('Low', 'Medium', 'High')),
                    survey_day INTEGER NOT NULL CHECK (survey_day IN (30, 60, 90)),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(code_hash, survey_day)
                );
                CREATE INDEX idx_survey_completion_date ON survey_responses(completion_date);
            """)
            print("✓ Created survey_responses table")
            
            # Create daily_active_users
            cur.execute("""
                CREATE TABLE daily_active_users (
                    id SERIAL PRIMARY KEY,
                    event_date DATE NOT NULL,
                    event_hour INTEGER NOT NULL CHECK (event_hour >= 0 AND event_hour < 24),
                    launch_count INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(event_date, event_hour)
                );
                CREATE INDEX idx_dau_event_date ON daily_active_users(event_date);
            """)
            print("✓ Created daily_active_users table")
            
            # Create daily_launch_tracker
            cur.execute("""
                CREATE TABLE daily_launch_tracker (
                    id SERIAL PRIMARY KEY,
                    code_hash VARCHAR(64) NOT NULL,
                    launch_date DATE NOT NULL DEFAULT CURRENT_DATE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(code_hash, launch_date)
                );
                CREATE INDEX idx_tracker_launch_date ON daily_launch_tracker(launch_date);
            """)
            print("✓ Created daily_launch_tracker table")
            
            conn.commit()
            print("✅ All analytics tables created successfully")
            
        conn.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        raise

if __name__ == "__main__":
    create_analytics_tables()
