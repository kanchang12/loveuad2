from flask import Flask, request, jsonify, send_file, render_template
from flask_cors import CORS
from datetime import datetime, timedelta
from PIL import Image
import io
import base64
import qrcode
import logging
import os
import google.generativeai as genai
import json
from config import Config
from db_manager import DatabaseManager
from rag_pipeline import RAGPipeline
from encryption import encrypt_data, decrypt_data, generate_patient_code, hash_patient_code
from pii_filter import PIIFilter

# Initialize Flask app
app = Flask(__name__)
app.secret_key = Config.SECRET_KEY
CORS(app, supports_credentials=True)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize database and RAG
db_manager = DatabaseManager()
rag_pipeline = RAGPipeline(db_manager)

# Create analytics tables on startup
def init_analytics_tables():
    """Create analytics tables if they don't exist"""
    try:
        with db_manager.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS survey_responses (
                    id SERIAL PRIMARY KEY,
                    code_hash VARCHAR(64) NOT NULL,
                    completion_date DATE NOT NULL DEFAULT CURRENT_DATE,
                    result_bucket VARCHAR(10) NOT NULL CHECK (result_bucket IN ('Low', 'Medium', 'High')),
                    survey_day INTEGER NOT NULL CHECK (survey_day IN (30, 60, 90)),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(code_hash, survey_day)
                );
                CREATE INDEX IF NOT EXISTS idx_survey_completion_date ON survey_responses(completion_date);
                
                CREATE TABLE IF NOT EXISTS daily_active_users (
                    id SERIAL PRIMARY KEY,
                    event_date DATE NOT NULL,
                    event_hour INTEGER NOT NULL CHECK (event_hour >= 0 AND event_hour < 24),
                    launch_count INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(event_date, event_hour)
                );
                CREATE INDEX IF NOT EXISTS idx_dau_event_date ON daily_active_users(event_date);
                
                CREATE TABLE IF NOT EXISTS daily_launch_tracker (
                    id SERIAL PRIMARY KEY,
                    code_hash VARCHAR(64) NOT NULL,
                    launch_date DATE NOT NULL DEFAULT CURRENT_DATE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(code_hash, launch_date)
                );
                CREATE INDEX IF NOT EXISTS idx_tracker_launch_date ON daily_launch_tracker(launch_date);
            """)
            db_manager.conn.commit()
            logger.info("✓ Analytics tables initialized")
    except Exception as e:
        logger.warning(f"Analytics tables already exist or error: {e}")

init_analytics_tables()

# Initialize Gemini API
if not Config.GEMINI_API_KEY:
    logger.warning("⚠️ GEMINI_API_KEY not set - OCR and AI features will fail")
    vision_model = None
else:
    genai.configure(api_key=Config.GEMINI_API_KEY)
    vision_model = genai.GenerativeModel(Config.VISION_MODEL)

# PII Filter instance
pii_filter = PIIFilter()

# ==================== PATIENT MANAGEMENT ====================

@app.route('/api/patient/register', methods=['POST'])
def register_patient():
    """Register new patient with 17-character code"""
    try:
        data = request.json
        
        # Generate unique code (17-char format: XXXX-XXXX-XXXX-XXXX-X)
        patient_code = generate_patient_code()
        code_hash = hash_patient_code(patient_code)
        
        logger.info(f"Registering new patient - Code: {patient_code}, Hash: {code_hash[:10]}...")
        
        # Encrypt patient data with tier
        patient_data = {
            'firstName': data.get('firstName'),
            'lastName': data.get('lastName', ''),
            'age': data.get('age'),
            'gender': data.get('gender'),
            'tier': data.get('tier', 'premium'),  # Default to premium
            'createdAt': datetime.utcnow().isoformat()
        }
        
        encrypted_data = encrypt_data(patient_data)
        
        # Store in database
        db_manager.insert_patient_data(code_hash, encrypted_data)
        
        logger.info(f"Patient registered successfully: {patient_data.get('firstName')}")
        
        return jsonify({
            'success': True,
            'patientCode': patient_code,
            'codeHash': code_hash,
            'tier': patient_data['tier']
        }), 201
    
    except Exception as e:
        logger.error(f"Registration error: {str(e)}", exc_info=True)
        return jsonify({'error': f'Registration failed: {str(e)}'}), 500

@app.route('/patient/register', methods=['POST'])
def register_patient_noapi():
    return register_patient()

@app.route('/api/patient/login', methods=['POST'])
def login_patient():
    """Login with patient code (17-character format: XXXX-XXXX-XXXX-XXXX-X)"""
    try:
        data = request.json
        patient_code = data.get('patientCode')
        
        if not patient_code:
            return jsonify({'error': 'Patient code required'}), 400
        
        # Clean and validate code format
        clean_code = patient_code.replace('-', '').strip().upper()
        
        # Only accept 17-char format (XXXX-XXXX-XXXX-XXXX-X)
        if len(clean_code) != 17:
            logger.warning(f"Invalid code length: {len(clean_code)} chars (expected 17)")
            return jsonify({'error': f'Invalid code format. Expected 17 characters (XXXX-XXXX-XXXX-XXXX-X), got {len(clean_code)}'}), 400
        
        code_hash = hash_patient_code(patient_code)
        logger.info(f"Login attempt - Code: {patient_code}, Clean: {clean_code}, Hash: {code_hash[:10]}...")
        
        # Verify code exists
        patient = db_manager.get_patient_data(code_hash)
        
        if not patient:
            logger.warning(f"Patient not found - Code: {patient_code}, Hash: {code_hash}")
            logger.warning(f"This means either: 1) Code doesn't exist, or 2) Code was created with old format")
            return jsonify({
                'error': 'Invalid patient code - not found in database. If you registered before, please register again with the new 17-character format.'
            }), 404
        
        # Decrypt patient data
        patient_data = decrypt_data(patient['encrypted_data'])
        
        if not patient_data:
            logger.error("Failed to decrypt patient data")
            return jsonify({'error': 'Data decryption failed'}), 500
        
        logger.info(f"Login successful for patient: {patient_data.get('firstName')}")
        
        return jsonify({
            'success': True,
            'codeHash': code_hash,
            'patient': {
                'firstName': patient_data.get('firstName'),
                'lastName': patient_data.get('lastName'),
                'age': patient_data.get('age'),
                'gender': patient_data.get('gender'),
                'tier': patient_data.get('tier', 'premium')
            }
        }), 200
    
    except Exception as e:
        logger.error(f"Login error: {str(e)}", exc_info=True)
        return jsonify({'error': f'Login failed: {str(e)}'}), 500

@app.route('/api/patient/qr/<code>', methods=['GET'])
def generate_qr(code):
    """Generate QR code for patient code"""
    try:
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(code)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        
        img_io = io.BytesIO()
        img.save(img_io, 'PNG')
        img_io.seek(0)
        
        return send_file(img_io, mimetype='image/png')
    
    except Exception as e:
        logger.error(f"QR generation error: {e}")
        return jsonify({'error': 'QR generation failed'}), 500

# ==================== MEDICATION MANAGEMENT ====================

@app.route('/api/medications/add', methods=['POST'])
def add_medication():
    """Add medication for patient"""
    try:
        data = request.json
        code_hash = data.get('codeHash')
        medication = data.get('medication')
        
        if not code_hash or not medication:
            return jsonify({'error': 'Missing required fields'}), 400
        
        # Verify patient exists
        patient = db_manager.get_patient_data(code_hash)
        if not patient:
            return jsonify({'error': 'Invalid patient code'}), 404
        
        # Add timestamp
        medication['createdAt'] = datetime.utcnow().isoformat()
        
        # Encrypt and store
        encrypted_data = encrypt_data(medication)
        db_manager.insert_medication(code_hash, encrypted_data)
        
        return jsonify({'success': True, 'message': 'Medication added'}), 201
    
    except Exception as e:
        logger.error(f"Add medication error: {e}")
        return jsonify({'error': 'Failed to add medication'}), 500

@app.route('/api/papers/count', methods=['GET'])
def get_papers_count():
    """Get total paper count"""
    try:
        stats = db_manager.get_stats()
        return jsonify({'success': True, 'totalPapers': stats['total_papers']}), 200
    except Exception as e:
        logger.error(f"Count error: {e}")
        return jsonify({'error': 'Failed'}), 500

@app.route('/api/papers/random', methods=['GET'])
def get_random_paper():
    """Get a random paper number"""
    try:
        with db_manager.conn.cursor() as cur:
            cur.execute("SELECT MAX(id) FROM research_papers;")
            max_id = cur.fetchone()['max']
        
        import random
        return jsonify({'success': True, 'paperId': random.randint(1, max_id)}), 200
    except Exception as e:
        logger.error(f"Random error: {e}")
        return jsonify({'error': 'Failed'}), 500

@app.route('/api/papers/<int:paper_id>', methods=['GET'])
def get_paper(paper_id):
    """Get paper by ID"""
    try:
        with db_manager.conn.cursor() as cur:
            cur.execute("""
                SELECT id, title, authors, journal, year, doi, abstract, full_text, created_at
                FROM research_papers
                WHERE id = %s;
            """, (paper_id,))
            
            paper = cur.fetchone()
        
        if not paper:
            return jsonify({'error': 'Paper not found'}), 404
        
        result = {
            'paperId': paper['id'],
            'title': paper['title'] or 'Untitled',
            'authors': paper['authors'] or 'Unknown',
            'journal': paper['journal'] or 'N/A',
            'year': paper['year'] or 'N/A',
            'doi': paper['doi'] or 'N/A',
            'abstract': paper['abstract'] or '',
            'fullText': paper['full_text'] or '',
            'hasFullText': bool(paper['full_text']),
            'ingestedAt': paper['created_at']
        }
        
        return jsonify({'success': True, 'paper': result}), 200
    
    except Exception as e:
        logger.error(f"Get paper error: {e}")
        return jsonify({'error': 'Failed to fetch paper'}), 500

@app.route('/api/medications/<code_hash>', methods=['GET'])
def get_medications(code_hash):
    """Get all active medications for patient with today's adherence status"""
    try:
        medications = db_manager.get_medications(code_hash)
        
        decrypted_meds = [decrypt_data(med['encrypted_data']) for med in medications]
        
        # Get adherence data for today
        patient = db_manager.get_patient_data(code_hash)
        if patient:
            patient_data = decrypt_data(patient['encrypted_data'])
            adherence_history = patient_data.get('medicationAdherence', [])
            
            # Get today's date
            today = datetime.now().strftime('%Y-%m-%d')
            today_adherence = [a for a in adherence_history if a.get('date') == today]
            
            # Add adherence status to each medication time
            for med in decrypted_meds:
                if 'times' in med:
                    for i, time in enumerate(med['times']):
                        # Check if this medication at this time was taken today
                        taken = any(
                            a.get('medication') == med['name'] and 
                            a.get('scheduledTime') == time 
                            for a in today_adherence
                        )
                        # Add taken status to each time slot
                        if 'takenStatus' not in med:
                            med['takenStatus'] = {}
                        med['takenStatus'][time] = taken
        
        return jsonify({
            'success': True,
            'medications': decrypted_meds
        }), 200
    
    except Exception as e:
        logger.error(f"Get medications error: {e}")
        return jsonify({'error': 'Failed to fetch medications'}), 500

@app.route('/api/medications/update', methods=['POST'])
def update_medication():
    """Update medication"""
    try:
        data = request.json
        code_hash = data.get('codeHash')
        medication = data.get('medication')
        
        if not code_hash or not medication:
            return jsonify({'error': 'Missing required fields'}), 400
        
        # Get all medications
        medications = db_manager.get_medications(code_hash)
        
        # Find and update the medication
        for med_record in medications:
            decrypted = decrypt_data(med_record['encrypted_data'])
            if decrypted['name'] == medication['name']:
                medication['updatedAt'] = datetime.utcnow().isoformat()
                encrypted_data = encrypt_data(medication)
                
                with db_manager.conn.cursor() as cur:
                    cur.execute("""
                        UPDATE medications 
                        SET encrypted_data = %s 
                        WHERE id = %s;
                    """, (encrypted_data, med_record['id']))
                    db_manager.conn.commit()
                
                return jsonify({'success': True, 'message': 'Medication updated'}), 200
        
        return jsonify({'error': 'Medication not found'}), 404
    
    except Exception as e:
        logger.error(f"Update medication error: {e}")
        return jsonify({'error': 'Failed to update medication'}), 500

@app.route('/api/medications/delete', methods=['POST'])
def delete_medication():
    """Mark medication as inactive"""
    try:
        data = request.json
        code_hash = data.get('codeHash')
        medication_name = data.get('medicationName')
        
        if not code_hash or not medication_name:
            return jsonify({'error': 'Missing required fields'}), 400
        
        medications = db_manager.get_medications(code_hash)
        
        for med_record in medications:
            decrypted = decrypt_data(med_record['encrypted_data'])
            if decrypted['name'] == medication_name:
                with db_manager.conn.cursor() as cur:
                    cur.execute("""
                        UPDATE medications 
                        SET active = FALSE 
                        WHERE id = %s;
                    """, (med_record['id'],))
                    db_manager.conn.commit()
                
                return jsonify({'success': True, 'message': 'Medication deleted'}), 200
        
        return jsonify({'error': 'Medication not found'}), 404
    
    except Exception as e:
        logger.error(f"Delete medication error: {e}")
        return jsonify({'error': 'Failed to delete medication'}), 500

# ==================== PRESCRIPTION SCANNING ====================

@app.route('/api/scan/prescription', methods=['POST'])
def scan_prescription():
    """Scan prescription using Gemini Vision with PII filtering - NO DIAGNOSIS"""
    try:
        data = request.json
        image_data = data.get('image')
        code_hash = data.get('codeHash')
        
        if not image_data or not code_hash:
            return jsonify({'error': 'Missing required fields'}), 400
        
        # Verify patient
        patient = db_manager.get_patient_data(code_hash)
        if not patient:
            return jsonify({'error': 'Invalid patient code'}), 404
        
        # Decode base64 image
        image_bytes = base64.b64decode(image_data.split(',')[1] if ',' in image_data else image_data)
        
        # OCR with Gemini Vision
        prompt = """Extract medication information from this prescription image.

Return ONLY the following information in this exact format:
Medication Name: [name]
Dosage: [dosage]
Frequency: [frequency]
Instructions: [instructions]

Do not include any patient names, addresses, or personal information."""
        
        # Convert bytes to PIL Image
        image = Image.open(io.BytesIO(image_bytes))
        response = vision_model.generate_content([prompt, image])
        
        ocr_text = response.text
        
        # Filter PII
        filtered_text = pii_filter.remove_pii(ocr_text)
        
        # AI Analysis with Gemini - NO DIAGNOSIS VERSION
        analysis_prompt = f"""Analyze this prescription and provide CAREGIVING GUIDANCE ONLY.

CRITICAL: You CANNOT diagnose conditions or interpret symptoms. You can ONLY provide:
- Medication management tips
- Safety information
- Storage guidance
- What healthcare professionals typically advise

Prescription Text:
{filtered_text}

Provide ONLY:
1. Medication summary (what it is commonly prescribed for - general info only)
2. Important safety warnings
3. Common considerations healthcare professionals mention
4. Storage instructions
5. Practical tips for dementia caregivers

DO NOT:
- Diagnose any condition
- Interpret why this was prescribed for this specific patient
- Make medical recommendations

Always end with: "Consult the prescribing doctor for questions about this medication."

Be concise and practical."""
        
        analysis_response = vision_model.generate_content(analysis_prompt)
        ai_analysis = analysis_response.text
        
        # Store as health record
        record_metadata = {
            'type': 'prescription_scan',
            'ocr_text': filtered_text,
            'ai_analysis': ai_analysis,
            'scanned_at': datetime.utcnow().isoformat()
        }
        
        encrypted_metadata = encrypt_data(record_metadata)
        db_manager.insert_health_record(code_hash, 'prescription', encrypted_metadata)
        
        return jsonify({
            'success': True,
            'ocr_text': filtered_text,
            'ai_analysis': ai_analysis,
            'disclaimer': '⚠️ This is NOT medical advice. Consult your healthcare provider for all medical questions.'
        }), 200
    
    except Exception as e:
        logger.error(f"Prescription scan error: {e}")
        return jsonify({'error': 'Scan failed'}), 500



# ==================== HEALTH RECORDS ====================

@app.route('/api/health/records/<code_hash>', methods=['GET'])
def get_health_records(code_hash):
    """Get health records for patient"""
    try:
        records = db_manager.get_health_records(code_hash)
        
        decrypted_records = [{
            'id': r['id'],
            'recordType': r['record_type'],
            'metadata': decrypt_data(r['encrypted_metadata']),
            'createdAt': r['created_at']
        } for r in records]
        
        return jsonify({
            'success': True,
            'records': decrypted_records
        }), 200
    
    except Exception as e:
        logger.error(f"Get health records error: {e}")
        return jsonify({'error': 'Failed to fetch records'}), 500

# ==================== CAREGIVER CONNECTIONS ====================

@app.route('/api/caregiver/connect', methods=['POST'])
def connect_caregiver():
    """Connect caregiver to patient"""
    try:
        data = request.json
        caregiver_id = data.get('caregiverId')
        patient_code = data.get('patientCode')
        patient_nickname = data.get('patientNickname')
        
        if not caregiver_id or not patient_code:
            return jsonify({'error': 'Missing required fields'}), 400
        
        code_hash = hash_patient_code(patient_code)
        
        # Verify patient exists
        patient = db_manager.get_patient_data(code_hash)
        if not patient:
            return jsonify({'error': 'Invalid patient code'}), 404
        
        # Create connection
        with db_manager.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO caregiver_connections 
                (caregiver_id, patient_code_hash, patient_nickname)
                VALUES (%s, %s, %s);
            """, (caregiver_id, code_hash, patient_nickname))
            db_manager.conn.commit()
        
        return jsonify({'success': True}), 201
    
    except Exception as e:
        logger.error(f"Connection error: {e}")
        return jsonify({'error': 'Connection failed'}), 500

# ==================== DEMENTIA RAG ENDPOINTS ====================

@app.route('/api/dementia/query', methods=['POST', 'GET'])
def dementia_query():
    """Get dementia guidance with research citations - NO DIAGNOSIS"""
    try:
        data = request.json
        code_hash = data.get('codeHash')
        query = data.get('query')
        
        if not code_hash or not query:
            return jsonify({'error': 'Missing required fields'}), 400
        
        # Verify patient exists
        patient = db_manager.get_patient_data(code_hash)
        if not patient:
            return jsonify({'error': 'Invalid patient code'}), 404
        
        # SAFETY CHECK: Detect diagnosis requests
        diagnosis_keywords = [
            'diagnose', 'diagnosis', 'what does he have', 'what does she have',
            'what condition', 'what disease', 'what is wrong', 'does he have',
            'does she have', 'is this', 'is it', 'could this be'
        ]
        
        query_lower = query.lower()
        is_diagnosis_request = any(keyword in query_lower for keyword in diagnosis_keywords)
        
        if is_diagnosis_request:
            # Return polite decline for diagnosis requests
            return jsonify({
                'success': True,
                'answer': """I cannot provide medical diagnoses. Only qualified healthcare professionals can diagnose conditions after proper examination.

**What I can help with:**
- Practical caregiving strategies
- Daily care tips
- Managing behaviors
- Communication techniques
- Safety recommendations

**What you should do:**
Please consult with your loved one's doctor or healthcare team. They can:
- Conduct proper medical assessments
- Order appropriate tests
- Provide accurate diagnosis
- Recommend treatment plans

Would you like practical caregiving advice instead?""",
                'sources': [],
                'disclaimer': '⚠️ This system does not diagnose medical conditions. Always consult healthcare professionals for medical decisions.'
            }), 200
        
        # Get RAG response with safety-enhanced prompt
        rag_response = rag_pipeline.get_response(query)
        
        # Encrypt and store conversation
        encrypted_query = encrypt_data(query)
        encrypted_response = encrypt_data(rag_response['answer'])
        
        db_manager.insert_conversation(
            code_hash,
            encrypted_query,
            encrypted_response,
            json.dumps(rag_response['sources'])
        )
        
        return jsonify({
            'success': True,
            'answer': rag_response['answer'],
            'sources': rag_response['sources'],
            'disclaimer': '⚠️ This guidance is for caregiving support only. Always consult healthcare professionals for medical diagnosis and treatment decisions.'
        }), 200
    
    except Exception as e:
        logger.error(f"Dementia query error: {e}")
        return jsonify({'error': 'Query failed'}), 500

@app.route('/dementia/query', methods=['POST'])
def dementia_query_noapi():
    return dementia_query()

@app.route('/api/dementia/history/<code_hash>', methods=['GET'])
def dementia_history(code_hash):
    """Get conversation history"""
    try:
        # Verify patient
        patient = db_manager.get_patient_data(code_hash)
        if not patient:
            return jsonify({'error': 'Invalid patient code'}), 404
        
        # Get conversations
        conversations = db_manager.get_conversations(code_hash)
        
        # Decrypt conversations
        decrypted_conversations = [{
            'id': conv['id'],
            'query': decrypt_data(conv['encrypted_query']),
            'response': decrypt_data(conv['encrypted_response']),
            'sources': conv['sources'],
            'createdAt': conv['created_at']
        } for conv in conversations]
        
        return jsonify({
            'success': True,
            'conversations': decrypted_conversations
        }), 200
    
    except Exception as e:
        logger.error(f"Get history error: {e}")
        return jsonify({'error': 'Failed to fetch history'}), 500

@app.route('/api/dementia/stats', methods=['GET'])
def dementia_stats():
    """Get RAG database statistics"""
    try:
        stats = db_manager.get_stats()
        return jsonify({
            'success': True,
            'research_papers': stats['total_papers'],
            'indexed_chunks': stats['total_chunks']
        }), 200
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return jsonify({'error': 'Stats unavailable'}), 500

# ==================== WEB PAGES ====================

@app.route("/", methods=["GET"])
def landing_page():
    """Serve landing page"""
    return render_template("landing.html")

@app.route("/index.html", methods=["GET"])
def index_page():
    """Serve index page"""
    return render_template("index.html")




# ==================== HEALTH CHECK ====================

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'loveUAD API',
        'version': '1.0.0'
    }), 200

# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

# EXACT FIX FOR app.py
# Add this code RIGHT BEFORE the line: if __name__ == '__main__':

# ==================== DUPLICATE ROUTES WITHOUT /api/ PREFIX ====================
# These allow both mobile and web apps to work



@app.route('/patient/login', methods=['POST'])
def login_patient_noapi():
    return login_patient()

@app.route('/patient/qr/<code>', methods=['GET'])
def generate_qr_noapi(code):
    return generate_qr(code)

@app.route('/medications/add', methods=['POST'])
def add_medication_noapi():
    return add_medication()

@app.route('/medications/<code_hash>', methods=['GET'])
def get_medications_noapi(code_hash):
    return get_medications(code_hash)

@app.route('/medications/update', methods=['POST'])
def update_medication_noapi():
    return update_medication()

@app.route('/medications/delete', methods=['POST'])
def delete_medication_noapi():
    return delete_medication()

@app.route('/api/medications/schedule', methods=['POST'])
@app.route('/medications/schedule', methods=['POST'])
def schedule_medications_noapi():
    try:
        data = request.json
        code_hash = data.get('codeHash')
        medications = data.get('medications')
        
        if not code_hash or not medications:
            return jsonify({'error': 'Missing required fields'}), 400
        
        patient = db_manager.get_patient_data(code_hash)
        if not patient:
            return jsonify({'error': 'Invalid patient code'}), 404
        
        for med in medications:
            med['createdAt'] = datetime.utcnow().isoformat()
            encrypted_data = encrypt_data(med)
            db_manager.insert_medication(code_hash, encrypted_data)
        
        return jsonify({'success': True, 'message': f'{len(medications)} medications scheduled'}), 201
    except Exception as e:
        logger.error(f"Schedule error: {e}")
        return jsonify({'error': 'Failed'}), 500

@app.route('/scan/prescription', methods=['POST'])
def scan_prescription_noapi():
    return scan_prescription()

@app.route('/health/records/<code_hash>', methods=['GET'])
def get_health_records_noapi(code_hash):
    return get_health_records(code_hash)

@app.route('/api/health/record', methods=['POST'])
@app.route('/health/record', methods=['POST'])
def add_health_record_noapi():
    try:
        data = request.json
        code_hash = data.get('codeHash')
        record_type = data.get('recordType')
        record_date = data.get('recordDate')
        
        if not code_hash or not record_type:
            return jsonify({'error': 'Missing fields'}), 400
        
        patient = db_manager.get_patient_data(code_hash)
        if not patient:
            return jsonify({'error': 'Invalid code'}), 404
        
        metadata = {
            'recordType': record_type,
            'ocrText': data.get('ocrText', ''),
            'extractedData': data.get('extractedData', {}),
            'aiInsights': data.get('aiInsights', {}),
            'notes': data.get('notes', ''),
            'imageId': data.get('imageId', ''),
            'createdAt': datetime.utcnow().isoformat()
        }
        
        encrypted_metadata = encrypt_data(metadata)
        db_manager.insert_health_record(code_hash, encrypted_metadata, record_date)
        
        return jsonify({'success': True}), 201
    except Exception as e:
        logger.error(f"Record error: {e}")
        return jsonify({'error': 'Failed'}), 500

@app.route('/api/health/ocr', methods=['POST'])
def process_ocr_api():
    return process_ocr_noapi()

@app.route('/health/ocr', methods=['POST'])
def process_ocr_noapi():
    try:
        # Check if Gemini Vision is available
        if not vision_model:
            return jsonify({
                'error': 'Vision API not configured',
                'details': 'GEMINI_API_KEY environment variable is not set. Please configure it to enable OCR.'
            }), 503
        
        data = request.json
        image_data = data.get('imageData')
        patient_age = data.get('patientAge')
        patient_gender = data.get('patientGender')
        
        if not image_data:
            return jsonify({'error': 'Missing image'}), 400
        
        # Decode base64 image
        try:
            image_bytes = base64.b64decode(image_data.split(',')[1] if ',' in image_data else image_data)
            image = Image.open(io.BytesIO(image_bytes))
        except Exception as e:
            logger.error(f"Image decode error: {e}")
            return jsonify({'error': 'Invalid image data'}), 400
        
        # OCR with Gemini Vision - IMPROVED PROMPT
        prompt = """You are a medical prescription reader. Extract ALL medications from this image.

Look for:
- Drug names (in ANY font size or style - bold, regular, handwritten)
- Dosages (mg, ml, tablets, etc.)
- Frequency (how many times per day)
- Any instructions

CRITICAL: Extract EVERY medication you see, even if formatting is unclear.

Return in this EXACT format for EACH medication:
MEDICATION: [full drug name]
DOSAGE: [amount and unit]
FREQUENCY: [times per day - use number only like 1, 2, 3]
INSTRUCTIONS: [any special instructions or "As directed"]

Example:
MEDICATION: Aspirin
DOSAGE: 100mg
FREQUENCY: 1
INSTRUCTIONS: Take with food

Extract all medications now:"""
        
        try:
            response = vision_model.generate_content([prompt, image])
            ocr_text = response.text
            logger.info(f"OCR Raw Response: {ocr_text}")
        except Exception as e:
            logger.error(f"Gemini Vision API error: {e}")
            return jsonify({
                'error': 'OCR processing failed',
                'details': 'Vision API error - Check Gemini API key and quota'
            }), 500
        
        filtered_text = pii_filter.remove_pii(ocr_text)
        
        # IMPROVED PARSING - more flexible
        medications = []
        lines = filtered_text.split('\n')
        current_med = {}
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # More flexible keyword matching
            line_upper = line.upper()
            
            if 'MEDICATION:' in line_upper or 'DRUG:' in line_upper or 'MEDICINE:' in line_upper:
                if current_med and 'name' in current_med:
                    medications.append(current_med)
                # Extract name after colon
                name = line.split(':', 1)[1].strip() if ':' in line else line
                current_med = {'name': name}
                
            elif ('DOSAGE:' in line_upper or 'DOSE:' in line_upper) and current_med:
                dosage = line.split(':', 1)[1].strip() if ':' in line else line
                current_med['dosage'] = dosage
                
            elif ('FREQUENCY:' in line_upper or 'TIMES:' in line_upper or 'FREQ:' in line_upper) and current_med:
                freq_text = line.split(':', 1)[1].strip() if ':' in line else line
                # Extract number from text
                import re
                numbers = re.findall(r'\d+', freq_text)
                if numbers:
                    current_med['frequency'] = int(numbers[0])
                elif 'once' in freq_text.lower():
                    current_med['frequency'] = 1
                elif 'twice' in freq_text.lower():
                    current_med['frequency'] = 2
                elif 'three' in freq_text.lower() or 'thrice' in freq_text.lower():
                    current_med['frequency'] = 3
                else:
                    current_med['frequency'] = 1
                    
            elif ('INSTRUCTIONS:' in line_upper or 'INSTRUCTION:' in line_upper or 'NOTES:' in line_upper) and current_med:
                instructions = line.split(':', 1)[1].strip() if ':' in line else line
                current_med['instructions'] = instructions
        
        # Add last medication
        if current_med and 'name' in current_med:
            medications.append(current_med)
        
        # If no medications found with structured format, try to extract from free text
        if not medications:
            logger.warning("No structured medications found, attempting free-text extraction")
            # Ask Gemini to be more aggressive
            retry_prompt = f"""The previous extraction failed. This is a prescription image.

Your task: Find EVERY medication name visible in the image.

Original text extracted:
{ocr_text}

Return ONLY a simple list:
1. [Medication name] - [dosage if visible]
2. [Medication name] - [dosage if visible]

Be aggressive - extract anything that looks like a drug name."""
            
            try:
                retry_response = vision_model.generate_content([retry_prompt, image])
                retry_text = retry_response.text
                logger.info(f"Retry extraction: {retry_text}")
                
                # Parse numbered list
                for line in retry_text.split('\n'):
                    if line.strip() and any(c.isalpha() for c in line):
                        # Remove numbering
                        clean_line = re.sub(r'^\d+[\.\)]\s*', '', line.strip())
                        if '-' in clean_line:
                            parts = clean_line.split('-', 1)
                            medications.append({
                                'name': parts[0].strip(),
                                'dosage': parts[1].strip() if len(parts) > 1 else 'As directed',
                                'frequency': 1,
                                'instructions': 'As directed'
                            })
                        elif clean_line:
                            medications.append({
                                'name': clean_line,
                                'dosage': 'As directed',
                                'frequency': 1,
                                'instructions': 'As directed'
                            })
            except Exception as retry_error:
                logger.error(f"Retry extraction failed: {retry_error}")
        
        for med in medications:
            freq = med.get('frequency', 1)
            if freq == 1:
                med['times'] = ['09:00']
            elif freq == 2:
                med['times'] = ['09:00', '21:00']
            elif freq == 3:
                med['times'] = ['09:00', '14:00', '21:00']
            else:
                med['times'] = ['09:00', '13:00', '17:00', '21:00']
        
        # EXTRACT APPOINTMENT DATES from the scanned document
        appointment_info = None
        try:
            appointment_prompt = """Look at this medical document and extract ANY appointment or follow-up date mentioned.

Search for phrases like:
- "Next appointment"
- "Follow up"
- "Review date"
- "See you on"
- "Appointment on"
- Any date mentioned for future visits

If you find an appointment date, return ONLY:
APPOINTMENT_DATE: [DD/MM/YYYY or MM/DD/YYYY format]
APPOINTMENT_TYPE: [brief description like "Follow-up", "Review", "Consultation"]

If NO appointment date is found, return:
NO_APPOINTMENT_FOUND

Scan the document now:"""
            
            appointment_response = vision_model.generate_content([appointment_prompt, image])
            appointment_text = appointment_response.text.strip()
            logger.info(f"Appointment extraction: {appointment_text}")
            
            if 'NO_APPOINTMENT_FOUND' not in appointment_text:
                # Parse the appointment
                import re
                from dateutil import parser
                
                date_match = re.search(r'APPOINTMENT_DATE:\s*(.+)', appointment_text)
                type_match = re.search(r'APPOINTMENT_TYPE:\s*(.+)', appointment_text)
                
                if date_match:
                    date_str = date_match.group(1).strip()
                    try:
                        # Try to parse the date
                        appointment_date = parser.parse(date_str, dayfirst=True)
                        appointment_info = {
                            'date': appointment_date.strftime('%Y-%m-%d'),
                            'type': type_match.group(1).strip() if type_match else 'Appointment',
                            'found': True
                        }
                        logger.info(f"Appointment found: {appointment_info}")
                    except Exception as parse_error:
                        logger.warning(f"Could not parse appointment date: {date_str} - {parse_error}")
        except Exception as appt_error:
            logger.warning(f"Appointment extraction failed: {appt_error}")
            # Continue even if appointment extraction fails
        
        ai_insights = {'enabled': False}
        try:
            # NO DIAGNOSIS VERSION
            analysis_prompt = f"""Provide CAREGIVING GUIDANCE for these medications prescribed to a {patient_age} year old {patient_gender}.

CRITICAL RULES:
- DO NOT diagnose why these were prescribed
- DO NOT interpret the patient's condition
- ONLY provide general medication information and caregiving tips

Medications:
{filtered_text}

Provide ONLY:
1. Brief summary (general use of these medications - not patient-specific diagnosis)
2. Important safety considerations
3. Common side effects healthcare professionals mention
4. Potential interactions to discuss with doctor
5. Practical caregiving tips for medication management

Always remind: "Discuss all questions with the prescribing healthcare provider."

Be concise and focus on practical caregiving support."""
            
            analysis_response = vision_model.generate_content(analysis_prompt)
            ai_insights = {
                'enabled': True,
                'analysis': analysis_response.text,
                'model': 'gemini-1.5-flash',
                'age_group': f'{patient_age} years old',
                'personalized': True,
                'disclaimer': '⚠️ This is caregiving guidance only, NOT medical diagnosis. Consult healthcare providers for all medical decisions.'
            }
        except Exception as e:
            logger.error(f"AI analysis error: {e}")
            # Continue even if AI analysis fails - medications are already extracted
            ai_insights = {
                'enabled': False,
                'error': 'AI analysis unavailable'
            }
        
        return jsonify({
            'success': True,
            'ocrResult': {
                'raw_text': filtered_text,
                'extracted_data': {
                    'medications': medications,
                    'appointment': appointment_info
                },
                'ai_insights': ai_insights
            }
        }), 200
    except Exception as e:
        logger.error(f"OCR error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/health/medication-taken', methods=['POST'])
def record_medication_taken():
    """Record when a patient takes their medication"""
    try:
        data = request.json
        code_hash = data.get('codeHash')
        medication_name = data.get('medicationName')
        scheduled_time = data.get('scheduledTime')
        taken_at = data.get('takenAt')
        
        if not all([code_hash, medication_name, scheduled_time, taken_at]):
            return jsonify({'error': 'Missing required fields'}), 400
        
        # Store in health records
        patient = db_manager.get_patient_data(code_hash)
        if not patient:
            return jsonify({'error': 'Patient not found'}), 404
        
        adherence_record = {
            'medication': medication_name,
            'scheduledTime': scheduled_time,
            'takenAt': taken_at,
            'date': datetime.fromisoformat(taken_at.replace('Z', '+00:00')).strftime('%Y-%m-%d'),
            'status': 'taken'
        }
        
        # Get existing medication adherence records
        patient_data = decrypt_data(patient['encrypted_data'])
        adherence_history = patient_data.get('medicationAdherence', [])
        adherence_history.append(adherence_record)
        
        # Keep only last 90 days
        patient_data['medicationAdherence'] = adherence_history[-270:]  # 3 meds * 3 times * 30 days
        
        # Save back to database
        encrypted_data = encrypt_data(patient_data)
        with db_manager.conn.cursor() as cur:
            cur.execute(
                "UPDATE patients SET encrypted_data = %s WHERE code_hash = %s",
                (encrypted_data, code_hash)
            )
            db_manager.conn.commit()
        
        logger.info(f"Medication adherence recorded for patient {code_hash[:8]}...")
        return jsonify({'success': True}), 200
        
    except Exception as e:
        logger.error(f"Medication adherence tracking error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/health/medication-adherence/<code_hash>', methods=['GET'])
def get_medication_adherence(code_hash):
    """Get medication adherence history for a patient"""
    try:
        patient = db_manager.get_patient_data(code_hash)
        if not patient:
            return jsonify({'error': 'Patient not found'}), 404
        
        patient_data = decrypt_data(patient['encrypted_data'])
        adherence_history = patient_data.get('medicationAdherence', [])
        
        # Calculate adherence statistics
        today = datetime.now().strftime('%Y-%m-%d')
        last_7_days = [(datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7)]
        
        recent_records = [r for r in adherence_history if r['date'] in last_7_days]
        
        stats = {
            'totalRecords': len(adherence_history),
            'last7Days': len(recent_records),
            'todayRecords': len([r for r in adherence_history if r['date'] == today]),
            'history': adherence_history[-50:]  # Last 50 records
        }
        
        return jsonify({'success': True, 'adherence': stats}), 200
        
    except Exception as e:
        logger.error(f"Get adherence error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/health/appointments/add', methods=['POST'])
def add_appointment():
    """Add an appointment for a patient"""
    try:
        data = request.json
        code_hash = data.get('codeHash')
        appointment = data.get('appointment')
        
        if not all([code_hash, appointment]):
            return jsonify({'error': 'Missing required fields'}), 400
        
        patient = db_manager.get_patient_data(code_hash)
        if not patient:
            return jsonify({'error': 'Patient not found'}), 404
        
        patient_data = decrypt_data(patient['encrypted_data'])
        appointments = patient_data.get('appointments', [])
        
        # Add new appointment
        appointment['id'] = 'appt-' + str(int(datetime.now().timestamp() * 1000))
        appointment['createdAt'] = datetime.now().isoformat()
        appointments.append(appointment)
        
        patient_data['appointments'] = appointments
        
        # Save to database
        encrypted_data = encrypt_data(patient_data)
        with db_manager.conn.cursor() as cur:
            cur.execute(
                "UPDATE patients SET encrypted_data = %s WHERE code_hash = %s",
                (encrypted_data, code_hash)
            )
            db_manager.conn.commit()
        
        logger.info(f"Appointment added for patient {code_hash[:8]}...")
        return jsonify({'success': True, 'appointment': appointment}), 200
        
    except Exception as e:
        logger.error(f"Add appointment error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/health/appointments/<code_hash>', methods=['GET'])
def get_appointments(code_hash):
    """Get all appointments for a patient"""
    try:
        patient = db_manager.get_patient_data(code_hash)
        if not patient:
            return jsonify({'error': 'Patient not found'}), 404
        
        patient_data = decrypt_data(patient['encrypted_data'])
        appointments = patient_data.get('appointments', [])
        
        return jsonify({'success': True, 'appointments': appointments}), 200
        
    except Exception as e:
        logger.error(f"Get appointments error: {e}")
        return jsonify({'error': str(e)}), 500

# ==================== FEATURE 1: ANONYMOUS MONTHLY CAREGIVER BURDEN SURVEY ====================

@app.route('/api/survey/check-eligibility/<code_hash>', methods=['GET'])
def check_survey_eligibility(code_hash):
    """
    Check if the caregiver is eligible for a survey (Day 30, 60, 90, etc.)
    Returns survey day if eligible, None if not
    """
    try:
        patient = db_manager.get_patient_data(code_hash)
        if not patient:
            return jsonify({'error': 'Patient not found'}), 404
        
        # Calculate account age in days
        created_at = patient.get('created_at')
        if not created_at:
            return jsonify({'eligible': False}), 200
        
        account_age_days = (datetime.now() - created_at).days
        
        # Check if account is at a 30-day milestone
        if account_age_days < 30:
            return jsonify({'eligible': False, 'accountAgeDays': account_age_days}), 200
        
        # Calculate which survey milestone (30, 60, 90, 120, etc.)
        survey_day = (account_age_days // 30) * 30
        
        # Check if survey already completed for this milestone
        with db_manager.conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM survey_responses WHERE code_hash = %s AND survey_day = %s",
                (code_hash, survey_day)
            )
            existing_survey = cur.fetchone()
        
        if existing_survey:
            return jsonify({'eligible': False, 'accountAgeDays': account_age_days, 'reason': 'Already completed'}), 200
        
        # Generate secure survey URL with encrypted code parameter
        survey_url = f"https://tally.so/r/wgEAQB?code={code_hash[:8]}&day={survey_day}"
        
        return jsonify({
            'eligible': True,
            'surveyDay': survey_day,
            'accountAgeDays': account_age_days,
            'surveyUrl': survey_url
        }), 200
        
    except Exception as e:
        logger.error(f"Survey eligibility check error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/survey/record-completion', methods=['POST'])
def record_survey_completion():
    """
    Record survey completion - ONLY stores code_hash, date, result bucket
    NO PII, NO text answers
    """
    try:
        data = request.json
        code_hash = data.get('codeHash')
        survey_day = data.get('surveyDay')
        result_bucket = data.get('resultBucket')  # 'Low', 'Medium', 'High'
        
        if not all([code_hash, survey_day, result_bucket]):
            return jsonify({'error': 'Missing required fields'}), 400
        
        if result_bucket not in ['Low', 'Medium', 'High']:
            return jsonify({'error': 'Invalid result bucket'}), 400
        
        # Store ONLY anonymous data
        with db_manager.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO survey_responses (code_hash, completion_date, result_bucket, survey_day)
                VALUES (%s, CURRENT_DATE, %s, %s)
                ON CONFLICT (code_hash, survey_day) DO NOTHING
            """, (code_hash, result_bucket, survey_day))
            db_manager.conn.commit()
        
        logger.info(f"Survey recorded: Day {survey_day}, Bucket: {result_bucket}")
        return jsonify({'success': True}), 200
        
    except Exception as e:
        logger.error(f"Survey recording error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/survey/aggregate-stats', methods=['GET'])
def get_survey_aggregate_stats():
    """
    Get aggregated survey statistics - NO individual data
    Returns mean reduction in burden scores
    """
    try:
        with db_manager.conn.cursor() as cur:
            # Get aggregated stats by bucket and survey day
            cur.execute("""
                SELECT 
                    survey_day,
                    result_bucket,
                    COUNT(*) as count
                FROM survey_responses
                GROUP BY survey_day, result_bucket
                ORDER BY survey_day, result_bucket
            """)
            results = cur.fetchall()
        
        stats = {
            'totalResponses': sum(r['count'] for r in results),
            'byDay': {},
            'byBucket': {'Low': 0, 'Medium': 0, 'High': 0}
        }
        
        for row in results:
            day = f"Day {row['survey_day']}"
            if day not in stats['byDay']:
                stats['byDay'][day] = {'Low': 0, 'Medium': 0, 'High': 0}
            stats['byDay'][day][row['result_bucket']] = row['count']
            stats['byBucket'][row['result_bucket']] += row['count']
        
        return jsonify({'success': True, 'stats': stats}), 200
        
    except Exception as e:
        logger.error(f"Survey stats error: {e}")
        return jsonify({'error': str(e)}), 500

# ==================== FEATURE 2: CUMULATIVE DAILY ACTIVE USER (DAU) TRACKING ====================

@app.route('/api/analytics/app-launch', methods=['POST'])
def record_app_launch():
    """
    Record app launch event - implements STRICT anonymization
    1. Check if code launched today (using temporary tracker)
    2. If unique launch, increment aggregated hourly count
    3. DISCARD the code immediately - only keep aggregated count
    """
    try:
        data = request.json
        code_hash = data.get('codeHash')
        
        if not code_hash:
            return jsonify({'error': 'Missing code'}), 400
        
        now = datetime.now()
        today = now.date()
        current_hour = now.hour
        
        with db_manager.conn.cursor() as cur:
            # Check if this code already launched today
            cur.execute("""
                SELECT 1 FROM daily_launch_tracker 
                WHERE code_hash = %s AND launch_date = %s
            """, (code_hash, today))
            already_launched_today = cur.fetchone()
            
            if already_launched_today:
                # Already counted for today - no action needed
                return jsonify({'success': True, 'counted': False}), 200
            
            # This is a UNIQUE daily launch - add to tracker
            cur.execute("""
                INSERT INTO daily_launch_tracker (code_hash, launch_date)
                VALUES (%s, %s)
                ON CONFLICT (code_hash, launch_date) DO NOTHING
            """, (code_hash, today))
            
            # Increment the AGGREGATED hourly count (NO code stored here)
            cur.execute("""
                INSERT INTO daily_active_users (event_date, event_hour, launch_count)
                VALUES (%s, %s, 1)
                ON CONFLICT (event_date, event_hour) 
                DO UPDATE SET launch_count = daily_active_users.launch_count + 1
            """, (today, current_hour))
            
            # CRITICAL: Clean up old tracker data (keep only last 2 days)
            cur.execute("""
                DELETE FROM daily_launch_tracker 
                WHERE launch_date < %s
            """, (today - timedelta(days=2),))
            
            db_manager.conn.commit()
        
        logger.info(f"DAU recorded: {today} {current_hour}:00 (Aggregated count incremented)")
        return jsonify({'success': True, 'counted': True}), 200
        
    except Exception as e:
        logger.error(f"App launch tracking error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/analytics/dau-stats', methods=['GET'])
def get_dau_stats():
    """
    Get aggregated DAU statistics
    PUBLIC WORDING COMPLIANT: "Cumulative Daily Active Users are tracked by counting 
    the total number of unique, daily 'App Launch' events generated by anonymous codes. 
    We do not track the identity of the user."
    """
    try:
        days = request.args.get('days', 30, type=int)
        start_date = datetime.now().date() - timedelta(days=days)
        
        with db_manager.conn.cursor() as cur:
            # Get daily totals
            cur.execute("""
                SELECT 
                    event_date,
                    SUM(launch_count) as daily_total
                FROM daily_active_users
                WHERE event_date >= %s
                GROUP BY event_date
                ORDER BY event_date DESC
            """, (start_date,))
            daily_stats = cur.fetchall()
            
            # Get hourly distribution (last 7 days)
            cur.execute("""
                SELECT 
                    event_hour,
                    AVG(launch_count) as avg_launches
                FROM daily_active_users
                WHERE event_date >= %s
                GROUP BY event_hour
                ORDER BY event_hour
            """, (datetime.now().date() - timedelta(days=7),))
            hourly_stats = cur.fetchall()
        
        stats = {
            'disclaimer': 'Cumulative Daily Active Users are tracked by counting the total number of unique, daily App Launch events generated by anonymous codes. We do not track the identity of the user.',
            'dailyTotals': [{'date': str(row['event_date']), 'count': row['daily_total']} for row in daily_stats],
            'hourlyAverage': [{'hour': row['event_hour'], 'avgCount': float(row['avg_launches'])} for row in hourly_stats],
            'totalDaysTracked': len(daily_stats)
        }
        
        return jsonify({'success': True, 'stats': stats}), 200
        
    except Exception as e:
        logger.error(f"DAU stats error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/caregiver/connect', methods=['POST'])
def connect_caregiver_noapi():
    return connect_caregiver()



@app.route('/dementia/history/<code_hash>', methods=['GET'])
def dementia_history_noapi(code_hash):
    return dementia_history(code_hash)

@app.route('/dementia/stats', methods=['GET'])
def dementia_stats_noapi():
    return dementia_stats()

@app.route('/patient/update-tier', methods=['POST'])
def update_patient_tier_noapi():
    try:
        data = request.json
        patient_code = data.get('patientCode')
        tier = data.get('tier')
        
        if not patient_code or tier not in ['free', 'premium']:
            return jsonify({'error': 'Invalid request'}), 400
        
        code_hash = hash_patient_code(patient_code)
        patient = db_manager.get_patient_data(code_hash)
        if not patient:
            return jsonify({'error': 'Invalid code'}), 404
        
        patient_data = decrypt_data(patient['encrypted_data'])
        patient_data['tier'] = tier
        patient_data['tierUpdatedAt'] = datetime.utcnow().isoformat()
        encrypted_data = encrypt_data(patient_data)
        
        with db_manager.conn.cursor() as cur:
            cur.execute("UPDATE patients SET encrypted_data = %s WHERE code_hash = %s;", 
                       (encrypted_data, code_hash))
            db_manager.conn.commit()
        
        return jsonify({'success': True, 'tier': tier}), 200
    except Exception as e:
        logger.error(f"Tier update error: {e}")
        return jsonify({'error': 'Failed'}), 500

# ==================== ADMIN PANEL - PASSWORD PROTECTED ====================

@app.route('/api/admin/check-tables', methods=['GET'])
def check_analytics_tables():
    """Debug endpoint to check if analytics tables exist and have data"""
    try:
        with db_manager.conn.cursor() as cur:
            tables_status = {}
            
            # Check each table
            for table in ['patients', 'caregivers', 'medications', 'survey_responses', 'daily_active_users', 'daily_launch_tracker']:
                try:
                    cur.execute(f"SELECT COUNT(*) as count FROM {table}")
                    count = cur.fetchone()['count']
                    tables_status[table] = {'exists': True, 'count': count}
                except Exception as e:
                    tables_status[table] = {'exists': False, 'error': str(e)}
            
            return jsonify({'success': True, 'tables': tables_status}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/analytics', methods=['GET'])
def admin_analytics_page():
    """
    Password-protected admin panel to view aggregated analytics
    Access: https://loveuad.com/admin/analytics
    """
    return render_template('admin_analytics.html')

@app.route('/api/admin/verify-password', methods=['POST'])
def verify_admin_password():
    """Verify admin password - stored in environment variable for security"""
    try:
        data = request.json
        password = data.get('password')
        
        # Admin password from environment variable (set in Cloud Run)
        admin_password = os.environ.get('ADMIN_PASSWORD', 'LoveUAD2025!Admin')
        
        if password == admin_password:
            return jsonify({'success': True, 'token': 'authenticated'}), 200
        else:
            return jsonify({'success': False, 'error': 'Invalid password'}), 401
            
    except Exception as e:
        logger.error(f"Admin auth error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/dashboard-stats', methods=['GET'])
def get_admin_dashboard_stats():
    """
    Get comprehensive dashboard statistics for admin panel
    Shows: Total users, Active users (logged in last 7 days), Survey stats, DAU
    """
    try:
        with db_manager.conn.cursor() as cur:
            # Total patient accounts
            cur.execute("SELECT COUNT(*) as count FROM patients")
            total_patients = cur.fetchone()['count']
            
            # Total caregiver accounts
            cur.execute("SELECT COUNT(*) as count FROM caregivers")
            total_caregivers = cur.fetchone()['count']
            
            # Active users (logged in last 7 days) - from daily_launch_tracker
            cur.execute("""
                SELECT COUNT(DISTINCT code_hash) as count 
                FROM daily_launch_tracker 
                WHERE launch_date >= CURRENT_DATE - INTERVAL '7 days'
            """)
            active_last_7_days = cur.fetchone()
            active_users = active_last_7_days['count'] if active_last_7_days else 0
            
            # Survey statistics
            cur.execute("""
                SELECT 
                    COUNT(DISTINCT code_hash) as unique_respondents,
                    COUNT(*) as total_responses,
                    survey_day,
                    result_bucket,
                    COUNT(*) as count
                FROM survey_responses
                GROUP BY survey_day, result_bucket
                ORDER BY survey_day, result_bucket
            """)
            survey_data = cur.fetchall()
            
            # DAU statistics (last 30 days)
            cur.execute("""
                SELECT 
                    event_date,
                    SUM(launch_count) as daily_total
                FROM daily_active_users
                WHERE event_date >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY event_date
                ORDER BY event_date DESC
            """)
            dau_daily = cur.fetchall()
            
            # DAU by hour (last 7 days)
            cur.execute("""
                SELECT 
                    event_hour,
                    AVG(launch_count) as avg_launches,
                    MAX(launch_count) as peak_launches
                FROM daily_active_users
                WHERE event_date >= CURRENT_DATE - INTERVAL '7 days'
                GROUP BY event_hour
                ORDER BY event_hour
            """)
            dau_hourly = cur.fetchall()
            
            # Medication adherence statistics
            cur.execute("""
                SELECT 
                    COUNT(*) as total_medications
                FROM medications
            """)
            total_meds = cur.fetchone()['count']
            
            # Recent survey responses (aggregated)
            cur.execute("""
                SELECT 
                    completion_date,
                    COUNT(*) as responses_count,
                    result_bucket
                FROM survey_responses
                WHERE completion_date >= CURRENT_DATE - INTERVAL '30 days'
                GROUP BY completion_date, result_bucket
                ORDER BY completion_date DESC
                LIMIT 50
            """)
            recent_surveys = cur.fetchall()
        
        # Process survey data
        survey_stats = {
            'unique_respondents': 0,
            'total_responses': 0,
            'by_day': {},
            'by_bucket': {'Low': 0, 'Medium': 0, 'High': 0}
        }
        
        if survey_data:
            survey_stats['unique_respondents'] = survey_data[0].get('unique_respondents', 0) if survey_data else 0
            survey_stats['total_responses'] = sum(r['count'] for r in survey_data)
            
            for row in survey_data:
                day_key = f"Day {row['survey_day']}"
                if day_key not in survey_stats['by_day']:
                    survey_stats['by_day'][day_key] = {'Low': 0, 'Medium': 0, 'High': 0, 'total': 0}
                survey_stats['by_day'][day_key][row['result_bucket']] = row['count']
                survey_stats['by_day'][day_key]['total'] += row['count']
                survey_stats['by_bucket'][row['result_bucket']] += row['count']
        
        # Calculate survey improvement metric
        improvement_percentage = 0
        if 'Day 30' in survey_stats['by_day'] and 'Day 90' in survey_stats['by_day']:
            day30_high = survey_stats['by_day']['Day 30'].get('High', 0)
            day30_total = survey_stats['by_day']['Day 30']['total']
            day90_high = survey_stats['by_day']['Day 90'].get('High', 0)
            day90_total = survey_stats['by_day']['Day 90']['total']
            
            if day30_total > 0 and day90_total > 0:
                day30_high_pct = (day30_high / day30_total) * 100
                day90_high_pct = (day90_high / day90_total) * 100
                improvement_percentage = day30_high_pct - day90_high_pct
        
        stats = {
            'accounts': {
                'total_patients': total_patients,
                'total_caregivers': total_caregivers,
                'active_users': active_users,
                'total_medications': total_meds
            },
            'survey': {
                **survey_stats,
                'improvement_percentage': round(improvement_percentage, 1),
                'recent_responses': [
                    {
                        'date': str(r['completion_date']),
                        'count': r['responses_count'],
                        'bucket': r['result_bucket']
                    } for r in recent_surveys
                ]
            },
            'dau': {
                'daily_totals': [
                    {'date': str(r['event_date']), 'count': r['daily_total']} 
                    for r in dau_daily
                ],
                'hourly_average': [
                    {
                        'hour': r['event_hour'], 
                        'avg': float(r['avg_launches']), 
                        'peak': r['peak_launches']
                    } 
                    for r in dau_hourly
                ],
                'total_days_tracked': len(dau_daily),
                'avg_daily_users': round(sum(r['daily_total'] for r in dau_daily) / len(dau_daily), 1) if dau_daily else 0
            }
        }
        
        return jsonify({'success': True, 'stats': stats}), 200
        
    except Exception as e:
        logger.error(f"Admin dashboard stats error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check_noapi():
    return health_check()

# ==================== CONTACT FORM ====================

@app.route('/api/contact', methods=['POST'])
def contact_form():
    """Handle contact form submissions and forward to Google Forms"""
    try:
        import requests
        
        data = request.json
        name = data.get('name', '')
        email = data.get('email', '')
        subject = data.get('subject', '')
        message = data.get('message', '')
        
        # Google Forms URL - we'll use iframe method
        # Create an invisible form submission
        google_form_url = "https://docs.google.com/forms/d/e/1FAIpQLSdGvoST8Q_FbQMhx3Va9CViypuhfp8dnbCqmXPTkXraX27Ljw/formResponse"
        
        # Note: You need to inspect your Google Form to get the correct entry IDs
        # For now, save to database as backup
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS contact_submissions (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    message TEXT,
                    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                INSERT INTO contact_submissions (name, email, subject, message)
                VALUES (%s, %s, %s, %s)
            """, (name, email, subject, message))
            conn.commit()
        
        logger.info(f"Contact form submission from {email}")
        
        # Return the Google Form URL for client-side submission
        return jsonify({
            'success': True, 
            'message': 'Thank you for your interest!',
            'redirect': f'https://docs.google.com/forms/d/e/1FAIpQLSdGvoST8Q_FbQMhx3Va9CViypuhfp8dnbCqmXPTkXraX27Ljw/formResponse?entry.NAME={name}&entry.EMAIL={email}&entry.SUBJECT={subject}&entry.MESSAGE={message}'
        }), 200
        
    except Exception as e:
        logger.error(f"Contact form error: {e}")
        return jsonify({'error': 'Failed to submit'}), 500

# ==================== END OF DUPLICATE ROUTES ====================

# ==================== MAIN ====================

if __name__ == '__main__':
    logger.info("="*60)
    logger.info("loveUAD - Privacy-First Dementia Care Support")
    logger.info("="*60)
    logger.info("17-digit anonymous codes")
    logger.info("End-to-end encryption")
    logger.info("PII filtering on scans")
    logger.info("RAG-powered dementia guidance")
    logger.info("Research-backed citations")
    logger.info("Google Cloud stack")
    logger.info("="*60)
    
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=False, host='0.0.0.0', port=port)
