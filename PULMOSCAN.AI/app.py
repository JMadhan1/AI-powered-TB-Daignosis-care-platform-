from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, send_from_directory
import os
import random
import json
import base64
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_caching import Cache
from functools import lru_cache
import sqlite3
from sqlite3 import Error
import cv2
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image
import io
import torch
import torchvision.transforms as transforms
from tensorflow.keras.applications.densenet import DenseNet121
from tensorflow.keras.layers import GlobalAveragePooling2D, Dense
from tensorflow.keras.models import Model
import tensorflow as tf

app = Flask(__name__)
app.secret_key = 'pulmoscan_secret_key'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Database setup
def create_connection():
    try:
        conn = sqlite3.connect('pulmoscan.db')
        return conn
    except Error as e:
        print(e)
        return None

def init_db():
    conn = create_connection()
    if conn is not None:
        try:
            c = conn.cursor()
            
            # Create patients table
            c.execute('''
                CREATE TABLE IF NOT EXISTS patients (
                    email TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    password TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create healthcare_workers table
            c.execute('''
                CREATE TABLE IF NOT EXISTS healthcare_workers (
                    email TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    password TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create patient_records table
            c.execute('''
                CREATE TABLE IF NOT EXISTS patient_records (
                    id TEXT PRIMARY KEY,
                    patient_email TEXT,
                    report_type TEXT,
                    report_data TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (patient_email) REFERENCES patients (email)
                )
            ''')
            
            conn.commit()
        except Error as e:
            print(e)
        finally:
            conn.close()

# Initialize database
init_db()

# Configure Flask-Caching
app.config['CACHE_TYPE'] = 'simple'
app.config['CACHE_DEFAULT_TIMEOUT'] = 300
cache = Cache(app)

# Preload common static files
STATIC_FILES = {
    'css': ['styles.css'],
    'js': ['main.js'],
    'images': ['logo.png']
}

# In-memory database with optimized data structures
users = {}
patients = {}
healthcare_workers = {}
patient_records = {}
accepted_patients = {}
cured_patients = {}

# Cache static templates
@lru_cache(maxsize=32)
def get_cached_template(template_name):
    return render_template(template_name)

# Serve static files efficiently
@app.route('/static/<path:filename>')
@cache.cached(timeout=3600)  # Cache for 1 hour
def serve_static(filename):
    return send_from_directory('static', filename)

# Cache the home page
@app.route('/')
@cache.cached(timeout=300)  # Cache for 5 minutes
def home():
    return render_template('home.html')

# Cache the features page
@app.route('/features')
@cache.cached(timeout=300)
def features():
    return render_template('features.html')

# Cache the contact page
@app.route('/contact')
@cache.cached(timeout=300)
def contact():
    return render_template('contact.html')

@app.route('/submit_contact', methods=['POST'])
def submit_contact():
    name = request.form.get('name')
    email = request.form.get('email')
    message = request.form.get('message')
    flash('Thank you for your message! We will get back to you soon.')
    return redirect(url_for('contact'))

# Optimize registration route
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        user_type = request.form.get('user_type')
        
        conn = create_connection()
        if conn is None:
            flash('System error. Please try again later.', 'error')
            return redirect(url_for('register'))
        
        try:
            c = conn.cursor()
            
            # Check if email already exists in either table
            c.execute('SELECT email FROM patients WHERE email = ?', (email,))
            if c.fetchone():
                flash('Email already registered!', 'error')
                return redirect(url_for('register'))
                
            c.execute('SELECT email FROM healthcare_workers WHERE email = ?', (email,))
            if c.fetchone():
                flash('Email already registered!', 'error')
                return redirect(url_for('register'))
            
            # Hash password
            hashed_password = generate_password_hash(password)
            
            if user_type == 'patient':
                # Insert into patients table
                c.execute('INSERT INTO patients (email, name, password) VALUES (?, ?, ?)',
                         (email, name, hashed_password))
                
                # Set session
                session['user_id'] = email
                session['user_type'] = 'patient'
                session['name'] = name
                
                conn.commit()
                flash('Registration successful!', 'success')
                return redirect(url_for('patient_dashboard'))
                
            elif user_type == 'healthcare':
                # Insert into healthcare_workers table
                c.execute('INSERT INTO healthcare_workers (email, name, password) VALUES (?, ?, ?)',
                         (email, name, hashed_password))
                
                # Set session
                session['user_id'] = email
                session['user_type'] = 'healthcare'
                session['name'] = name
                
                conn.commit()
                flash('Registration successful!', 'success')
                return redirect(url_for('healthcare_dashboard'))
            
            else:
                flash('Invalid user type!', 'error')
                return redirect(url_for('register'))
                
        except Error as e:
            print(e)
            flash('Error during registration. Please try again.', 'error')
            return redirect(url_for('register'))
        finally:
            conn.close()
    
    return render_template('register.html')

# Optimize login route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        conn = create_connection()
        if conn is None:
            flash('System error. Please try again later.', 'error')
            return redirect(url_for('login'))
        
        try:
            c = conn.cursor()
            
            # Try patients table first
            c.execute('SELECT * FROM patients WHERE email = ?', (email,))
            user = c.fetchone()
            user_type = 'patient'
            
            if not user:
                # Try healthcare_workers table
                c.execute('SELECT * FROM healthcare_workers WHERE email = ?', (email,))
                user = c.fetchone()
                user_type = 'healthcare'
            
            if user and check_password_hash(user[2], password):  # Index 2 is password
                session['user_id'] = email
                session['user_type'] = user_type
                session['name'] = user[1]  # Index 1 is name
                flash('Login successful!', 'success')
                return redirect(url_for(f'{user_type}_dashboard'))
            
            flash('Invalid email or password', 'error')
            return redirect(url_for('login'))
            
        except Error as e:
            print(e)
            flash('Error during login. Please try again.', 'error')
            return redirect(url_for('login'))
        finally:
            conn.close()
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

# Cache patient dashboard for 1 minute
@app.route('/patient_dashboard')
@cache.memoize(60)
def patient_dashboard():
    if 'user_id' not in session or session['user_type'] != 'patient':
        flash('Please login to access the dashboard', 'error')
        return redirect(url_for('login'))
    
    conn = create_connection()
    if conn is None:
        flash('System error. Please try again later.', 'error')
        return redirect(url_for('home'))
    
    try:
        c = conn.cursor()
        c.execute('SELECT * FROM patients WHERE email = ?', (session['user_id'],))
        patient = c.fetchone()
        
        c.execute('SELECT * FROM patient_records WHERE patient_email = ? ORDER BY created_at DESC',
                 (session['user_id'],))
        patient_records = c.fetchall()
        
        return render_template('patient_dashboard.html',
                             patient={'name': patient[1], 'email': patient[0]},
                             reports=patient_records)
    except Error as e:
        print(e)
        flash('Error loading dashboard. Please try again.', 'error')
        return redirect(url_for('home'))
    finally:
        conn.close()

# Cache healthcare dashboard for 1 minute
@app.route('/healthcare_dashboard')
@cache.memoize(60)
def healthcare_dashboard():
    if 'user_id' not in session or session['user_type'] != 'healthcare':
        flash('Please login to access the dashboard', 'error')
        return redirect(url_for('login'))
    
    conn = create_connection()
    if conn is None:
        flash('System error. Please try again later.', 'error')
        return redirect(url_for('home'))
    
    try:
        c = conn.cursor()
        c.execute('SELECT * FROM healthcare_workers WHERE email = ?', (session['user_id'],))
        healthcare = c.fetchone()
        
        # Get pending reports with patient details
        c.execute('''
            SELECT pr.*, p.name as patient_name 
            FROM patient_records pr 
            JOIN patients p ON pr.patient_email = p.email 
            WHERE pr.status = 'pending'
            ORDER BY pr.created_at DESC
        ''')
        pending_reports_raw = c.fetchall()
        
        # Format pending reports
        pending_reports = []
        for report in pending_reports_raw:
            report_data = json.loads(report[3])  # Parse the JSON stored in report_data column
            pending_reports.append({
                'id': report[0],
                'patient_email': report[1],
                'patient_name': report_data['patient_name'],
                'type': report[2],
                'data': report_data,
                'status': report[4],
                'created_at': report[5]
            })
        
        # Get accepted cases
        c.execute('''
            SELECT pr.*, p.name as patient_name 
            FROM patient_records pr 
            JOIN patients p ON pr.patient_email = p.email 
            WHERE pr.status = 'accepted'
            ORDER BY pr.created_at DESC
        ''')
        accepted_cases_raw = c.fetchall()
        
        # Format accepted cases
        accepted_cases = []
        for case in accepted_cases_raw:
            case_data = json.loads(case[3])
            accepted_cases.append({
                'id': case[0],
                'patient_email': case[1],
                'patient_name': case_data['patient_name'],
                'type': case[2],
                'data': case_data,
                'status': case[4],
                'created_at': case[5]
            })
        
        # Get cured cases
        c.execute('''
            SELECT pr.*, p.name as patient_name 
            FROM patient_records pr 
            JOIN patients p ON pr.patient_email = p.email 
            WHERE pr.status = 'cured'
            ORDER BY pr.created_at DESC
        ''')
        cured_cases_raw = c.fetchall()
        
        # Format cured cases
        cured_patients = []
        for case in cured_cases_raw:
            case_data = json.loads(case[3])
            cured_patients.append({
                'id': case[0],
                'patient_email': case[1],
                'patient_name': case_data['patient_name'],
                'type': case[2],
                'data': case_data,
                'status': case[4],
                'created_at': case[5],
                'treatment_duration': case_data.get('treatment_duration', 'N/A')
            })
        
        return render_template('healthcare_dashboard.html',
                             healthcare={'name': healthcare[1], 'email': healthcare[0]},
                             pending_reports=pending_reports,
                             accepted_cases=accepted_cases,
                             cured_patients=cured_patients)
    except Error as e:
        print(f"Error loading dashboard: {str(e)}")
        flash('Error loading dashboard. Please try again.', 'error')
        return redirect(url_for('home'))
    finally:
        if conn:
            conn.close()

@app.route('/upload_report', methods=['POST'])
def upload_report():
    if 'email' not in session:
        return jsonify({'error': 'Not authorized'}), 401
    
    try:
        conn = create_connection()
        cursor = conn.cursor()
        
        # Generate a unique report ID
        report_id = f"RPT{int(datetime.now().timestamp())}{random.randint(1000, 9999)}"
        
        # Handle X-ray upload
        xray_data = None
        if 'xray' in request.files:
            xray_file = request.files['xray']
            if xray_file.filename:
                # Read the image data
                image_data = xray_file.read()
                
                # Process the X-ray image
                analysis_result = process_xray_and_highlight(image_data)
                
                # Save the processed image
                filename = secure_filename(f"{report_id}_xray.jpg")
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                with open(filepath, 'wb') as f:
                    f.write(base64.b64decode(analysis_result['image_base64']))
                
                xray_data = {
                    'image_path': f"/static/uploads/{filename}",
                    'tb_probability': analysis_result['tb_probability'],
                    'infected_areas': analysis_result['infected_areas'],
                    'confidence_score': random.uniform(0.85, 0.95)
                }
        
        # Handle sputum test data
        sputum_data = None
        if request.form.get('sputum_test'):
            sputum_probability = float(request.form.get('sputum_probability', 0))
            # Correlate sputum test probability with X-ray data if available
            if xray_data:
                # Adjust sputum probability based on X-ray findings
                base_probability = xray_data['tb_probability']
                sputum_probability = max(min(base_probability * random.uniform(0.8, 1.2), 100), 0)
            
            sputum_data = {
                'result': 'positive' if sputum_probability > 50 else 'negative',
                'probability': sputum_probability,
                'details': request.form.get('sputum_details', '')
            }
        
        # Get symptoms data
        symptoms = request.form.get('symptoms', '{}')
        if isinstance(symptoms, str):
            symptoms = json.loads(symptoms)
        
        # Prepare report data
        report_data = {
            'report_id': report_id,
            'patient_email': session['email'],
            'report_type': request.form.get('report_type', 'xray'),
            'status': 'pending',
            'xray_data': xray_data,
            'sputum_data': sputum_data,
            'symptoms': symptoms,
            'upload_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # Insert into database
        cursor.execute('''
            INSERT INTO reports (report_id, patient_email, report_type, status, data, upload_date)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            report_data['report_id'],
            report_data['patient_email'],
            report_data['report_type'],
            report_data['status'],
            json.dumps(report_data),
            report_data['upload_date']
        ))
        
        conn.commit()
        return jsonify({'success': True, 'report_id': report_id})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if conn:
            conn.close()

@app.route('/get_pending_reports')
def get_pending_reports():
    if 'user_id' not in session or session['user_type'] != 'healthcare':
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    try:
        conn = create_connection()
        if conn is None:
            return jsonify({'success': False, 'message': 'Database error'})

        c = conn.cursor()
        
        # Get all pending reports with patient details
        c.execute('''
            SELECT pr.*, p.name as patient_name 
            FROM patient_records pr 
            JOIN patients p ON pr.patient_email = p.email 
            WHERE pr.status = 'pending'
            ORDER BY pr.created_at DESC
        ''')
        
        reports = []
        for row in c.fetchall():
            report_data = json.loads(row[3])  # report_data column
            reports.append({
                'id': row[0],
                'patient_email': row[1],
                'patient_name': report_data['patient_name'],
                'type': row[2],
                'data': report_data,
                'status': row[4],
                'date': row[5]
            })

        return jsonify({
            'success': True,
            'reports': reports
        })

    except Exception as e:
        print(f"Error getting pending reports: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error getting pending reports: {str(e)}'
        })
    finally:
        if conn:
            conn.close()

@app.route('/accept_report', methods=['POST'])
def accept_report():
    if 'user_id' not in session or session['user_type'] != 'healthcare':
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    try:
        data = request.get_json()
        report_id = data.get('report_id')
        
        if not report_id:
            return jsonify({'success': False, 'message': 'Report ID is required'})
        
        conn = create_connection()
        if conn is None:
            return jsonify({'success': False, 'message': 'Database error'})
        
        c = conn.cursor()
        
        # Get the current report data
        c.execute('SELECT * FROM patient_records WHERE id = ?', (report_id,))
        report = c.fetchone()
        
        if not report:
            return jsonify({'success': False, 'message': 'Report not found'})
        
        # Update the report status to accepted
        c.execute('''
            UPDATE patient_records 
            SET status = 'accepted',
                healthcare_id = ?,
                updated_at = ?
            WHERE id = ?
        ''', (
            session['user_id'],
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            report_id
        ))
        
        # Get the updated report data
        report_data = json.loads(report[3])
        report_data['accepted_by'] = session['user_id']
        report_data['accepted_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Update the report data
        c.execute('''
            UPDATE patient_records 
            SET report_data = ?
            WHERE id = ?
        ''', (json.dumps(report_data), report_id))
        
        # Create a notification for the patient
        c.execute('''
            INSERT INTO notifications (
                user_id, 
                message, 
                type, 
                created_at
            ) VALUES (?, ?, ?, ?)
        ''', (
            report_data['patient_email'],
            f"Your report has been accepted by Dr. {session['user_id']}. You can now view the analysis.",
            'report_accepted',
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ))
        
        conn.commit()
        
        return jsonify({
            'success': True,
            'message': 'Report accepted successfully',
            'data': {
                'report_id': report_id,
                'status': 'accepted',
                'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        })
        
    except Exception as e:
        print(f"Error accepting report: {str(e)}")
        return jsonify({'success': False, 'message': f'Error accepting report: {str(e)}'})
    finally:
        if conn:
            conn.close()

@app.route('/reject_report', methods=['POST'])
def reject_report():
    if 'user_id' not in session or session['user_type'] != 'healthcare':
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    try:
        data = request.get_json()
        report_id = data.get('report_id')
        
        conn = create_connection()
        if conn is None:
            return jsonify({'success': False, 'message': 'Database error'})

        c = conn.cursor()
        
        # Update report status
        c.execute('''
            UPDATE patient_records 
            SET status = 'rejected',
                healthcare_email = ?,
                updated_at = ?
            WHERE id = ?
        ''', (
            session['user_id'],
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            report_id
        ))
        
        conn.commit()

        return jsonify({
            'success': True,
            'message': 'Report rejected successfully'
        })

    except Exception as e:
        print(f"Error rejecting report: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error rejecting report: {str(e)}'
        })
    finally:
        if conn:
            conn.close()

@app.route('/get_analysis/<report_id>')
def get_analysis(report_id):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    try:
        conn = create_connection()
        if conn is None:
            return jsonify({'success': False, 'message': 'Database error'})

        c = conn.cursor()
        
        # Get the report data from the database
        c.execute('SELECT report_data FROM patient_records WHERE id = ?', (report_id,))
        result = c.fetchone()
        
        if not result:
            return jsonify({'success': False, 'message': 'Report not found'})
        
        # Parse the JSON data
        report_data = json.loads(result[0])
        
        # Format the analysis data
        analysis = {
            'xray': report_data.get('xray_data'),
            'sputum': report_data.get('sputum_data'),
            'symptoms': report_data.get('symptoms_data')
        }
        
        return jsonify({
            'success': True,
            'analysis': analysis
        })

    except Exception as e:
        print(f"Error retrieving analysis: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Error retrieving analysis: {str(e)}'
        })
    finally:
        if conn:
            conn.close()

@app.route('/accept_patient', methods=['POST'])
def accept_patient():
    if 'username' not in session or session['user_type'] != 'healthcare':
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    username = session['username']
    report_id = request.form.get('report_id')
    
    if report_id not in patient_records:
        return jsonify({'success': False, 'message': 'Report not found'})
    
    patient_records[report_id]['status'] = 'accepted'
    patient_records[report_id]['healthcare_worker'] = username
    
    if username not in accepted_patients:
        accepted_patients[username] = {}
    
    patient_username = patient_records[report_id]['patient']
    accepted_patients[username][patient_username] = patient_records[report_id]
    
    return jsonify({'success': True, 'message': 'Patient accepted'})

@app.route('/reject_patient', methods=['POST'])
def reject_patient():
    if 'username' not in session or session['user_type'] != 'healthcare':
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    report_id = request.form.get('report_id')
    
    if report_id not in patient_records:
        return jsonify({'success': False, 'message': 'Report not found'})
    
    patient_records[report_id]['status'] = 'rejected'
    
    return jsonify({'success': True, 'message': 'Patient rejected'})

@app.route('/mark_cured', methods=['POST'])
def mark_cured():
    if 'username' not in session or session['user_type'] != 'healthcare':
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    username = session['username']
    patient_username = request.form.get('patient_username')
    
    if username not in accepted_patients or patient_username not in accepted_patients[username]:
        return jsonify({'success': False, 'message': 'Patient not found'})
    
    if username not in cured_patients:
        cured_patients[username] = {}
    
    cured_patients[username][patient_username] = accepted_patients[username][patient_username]
    del accepted_patients[username][patient_username]
    
    return jsonify({'success': True, 'message': 'Patient marked as cured'})

@app.route('/get_medications')
def get_medications():
    if 'username' not in session or session['user_type'] != 'patient':
        return redirect(url_for('login'))
    
    username = session['username']
    medications = []
    
    for report_id in patients.get(username, {}).get('reports', []):
        if report_id in patient_records and 'analysis' in patient_records[report_id] and 'treatment' in patient_records[report_id]['analysis']:
            medications.extend(patient_records[report_id]['analysis']['treatment']['medications'])
    
    return jsonify({'success': True, 'medications': medications})

@app.route('/get_prevention_steps')
def get_prevention_steps():
    prevention_steps = [
        {
            'title': 'Cover Your Mouth',
            'description': 'Always cover your mouth and nose when coughing or sneezing with a tissue or your elbow.',
            'icon': 'fa-head-side-mask'
        },
        {
            'title': 'Ventilate Rooms',
            'description': 'Ensure good ventilation in all rooms by opening windows regularly.',
            'icon': 'fa-wind'
        },
        {
            'title': 'Complete Treatment',
            'description': 'Always complete the full course of TB medication, even if you start feeling better.',
            'icon': 'fa-pills'
        },
        {
            'title': 'Regular Check-ups',
            'description': 'Attend all scheduled medical appointments for monitoring your progress.',
            'icon': 'fa-stethoscope'
        },
        {
            'title': 'Healthy Diet',
            'description': 'Maintain a balanced diet rich in proteins, vitamins, and minerals to boost immunity.',
            'icon': 'fa-apple-whole'
        },
        {
            'title': 'Avoid Crowds',
            'description': 'Limit time in crowded places, especially during the infectious phase of TB.',
            'icon': 'fa-people-group'
        }
    ]
    
    return jsonify({'success': True, 'prevention_steps': prevention_steps})

@app.route('/get_tb_guidance')
def get_tb_guidance():
    guidance = {
        'english': {
            'title': 'Understanding Tuberculosis',
            'content': 'Tuberculosis (TB) is an infectious disease that usually affects the lungs. It is caused by the bacterium Mycobacterium tuberculosis and spreads through the air when an infected person coughs or sneezes. TB can be cured with a proper course of antibiotics.',
            'video_url': 'https://www.youtube.com/embed/example_english'
        },
        'kannada': {
            'title': 'ಕ್ಷಯರೋಗವನ್ನು ಅರ್ಥಮಾಡಿಕೊಳ್ಳುವುದು',
            'content': 'ಕ್ಷಯರೋಗವು (ಟಿಬಿ) ಸಾಮಾನ್ಯವಾಗಿ ಶ್ವಾಸಕೋಶಗಳನ್ನು ಪ್ರಭಾವಿಸುವ ಸಾಂಕ್ರಾಮಿಕ ರೋಗವಾಗಿದೆ. ಇದು ಮೈಕೋಬ್ಯಾಕ್ಟೀರಿಯಂ ಟ್ಯೂಬರ್ಕ್ಯುಲೋಸಿಸ್ ಬ್ಯಾಕ್ಟೀರಿಯಾದಿಂದ ಉಂಟಾಗುತ್ತದೆ ಮತ್ತು ಸೋಂಕಿತ ವ್ಯಕ್ತಿಯು ಕೆಮ್ಮಿದಾಗ ಅಥವಾ ಸೀನಿದಾಗ ಗಾಳಿಯ ಮೂಲಕ ಹರಡುತ್ತದೆ. ಸರಿಯಾದ ಆಂಟಿಬಯೋಟಿಕ್ಸ್ ಕೋರ್ಸ್‌ನೊಂದಿಗೆ ಟಿಬಿಯನ್ನು ಗುಣಪಡಿಸಬಹುದು.',
            'video_url': 'https://www.youtube.com/embed/example_kannada'
        },
        'telugu': {
            'title': 'క్షయవ్యాధిని అర్థం చేసుకోవడం',
            'content': 'క్షయవ్యాధి (టిబి) అనేది సాధారణంగా ఊపిరితిత్తులను ప్రభావితం చేస అంటువ్యాధి. ఇది మైకోబ్యాక్టీరియం టుబర్కులోసిస్ బ్యాక్టీరియా వల్ల కలుగుతుంది మరియు సోకిన వ్యక్తి దగ్గినప్పుడు లేదా తుమ్మినప్పుడు గాలి ద్వారా వ్యాపిస్తుంది. సరైన యాంటీబయాటిక్స్ కోర్సుతో టిబిని నయం చేయవచ్చు.',
            'video_url': 'https://www.youtube.com/embed/example_telugu'
        },
        'tamil': {
            'title': 'காசநோயைப் புரிந்துகொள்வது',
            'content': 'காசநோய் (டிபி) என்பது பொதுவாக நுரையீரல்களை பாதிக்கும் தொற்று நோய் ஆகும். இது மைக்கோபாக்டீரியம் டியூபர்குலோசிஸ் பாக்டீரியாவால் ஏற்படுகிறது மற்றும் தொற்று உள்ள நபர் இருமும்போது அல்லது தும்மும்போது காற்றின் மூலம் பரவுகிறது. சரியான ஆன்டிபயாடிக்ஸ் கோர்ஸ் மூலம் டிபியை குணப்படுத்தலாம்.',
            'video_url': 'https://www.youtube.com/embed/example_tamil'
        },
        'malayalam': {
            'title': 'ക്ഷയരോഗം മനസ്സിലാക്കുന്നു',
            'content': 'ക്ഷയരോഗം (ടിബി) സാധാരണയായി ശ്വാസകോശത്തെ ബാധിക്കുന്ന ഒരു പകർച്ചവ്യാധിയാണ്. ഇത് മൈക്കോബാക്ടീരിയം ട്യൂബർക്കുലോസിസ് ബാക്ടീരിയ മൂലമുണ്ടാകുന്നതും രോഗബാധിതനായ വ്യക്തി ചുമയ്ക്കുമ്പോഴോ തുമ്മുമ്പോഴോ വായു വഴി പടരുന്നതുമാണ്. ശരിയായ ആന്റിബയോട്ടിക്കുകളുടെ കോഴ്‌സ് ഉപയോഗിച്ച് ടിബി രോഗം ചികിത്സിക്കാം.',
            'video_url': 'https://www.youtube.com/embed/example_malayalam'
        }
    }
    
    language = request.args.get('language', 'english')
    if language not in guidance:
        language = 'english'
    
    return jsonify({'success': True, 'guidance': guidance[language]})

# HTML Templates as strings
@app.route('/get_html_template')
def get_html_template():
    template_name = request.args.get('template')
    if template_name == 'home':
        return '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pulmoscan.ai - TB Diagnosis & Care</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        /* Global Styles */
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        
        body {
            background-color: #f5f7fa;
            color: #333;
            line-height: 1.6;
        }
        
        .container {
            width: 90%;
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 20px;
        }
        
        /* Header Styles */
        header {
            background-color: #2c3e50;
            color: white;
            padding: 1rem 0;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        
        nav {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .logo {
            font-size: 1.8rem;
            font-weight: bold;
            color: #3498db;
        }
        
        .nav-links {
            display: flex;
            list-style: none;
        }
        
        .nav-links li {
            margin-left: 2rem;
        }
        
        .nav-links a {
            color: white;
            text-decoration: none;
            font-weight: 500;
            transition: color 0.3s;
        }
        
        .nav-links a:hover {
            color: #3498db;
        }
        
        /* Hero Section */
        .hero {
            background: linear-gradient(rgba(44, 62, 80, 0.8), rgba(44, 62, 80, 0.8)), url('https://placehold.co/1200x600');
            background-size: cover;
            background-position: center;
            color: white;
            text-align: center;
            padding: 5rem 0;
            margin-bottom: 3rem;
        }
        
        .hero h1 {
            font-size: 3rem;
            margin-bottom: 1rem;
        }
        
        .hero p {
            font-size: 1.2rem;
            max-width: 800px;
            margin: 0 auto 2rem;
        }
        
        .btn-container {
            display: flex;
            justify-content: center;
            gap: 1rem;
            margin-top: 2rem;
        }
        
        .btn {
            display: inline-block;
            background-color: #3498db;
            color: white;
            padding: 0.8rem 2rem;
            border-radius: 5px;
            text-decoration: none;
            font-weight: 600;
            transition: background-color 0.3s;
            border: none;
            cursor: pointer;
        }
        
        .btn:hover {
            background-color: #2980b9;
        }
        
        .btn-secondary {
            background-color: transparent;
            border: 2px solid #3498db;
        }
        
        .btn-secondary:hover {
            background-color: rgba(52, 152, 219, 0.1);
        }
        
        /* Features Section */
        .features {
            padding: 3rem 0;
            text-align: center;
        }
        
        .features h2 {
            font-size: 2.5rem;
            margin-bottom: 3rem;
            color: #2c3e50;
        }
        
        .feature-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 2rem;
        }
        
        .feature-card {
            background-color: white;
            border-radius: 10px;
            padding: 2rem;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            transition: transform 0.3s;
        }
        
        .feature-card:hover {
            transform: translateY(-10px);
        }
        
        .feature-icon {
            font-size: 3rem;
            color: #3498db;
            margin-bottom: 1rem;
        }
        
        .feature-card h3 {
            font-size: 1.5rem;
            margin-bottom: 1rem;
            color: #2c3e50;
        }
        
        /* Footer */
        footer {
            background-color: #2c3e50;
            color: white;
            padding: 2rem 0;
            text-align: center;
            margin-top: 3rem;
        }
        
        .footer-content {
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        
        .footer-links {
            display: flex;
            list-style: none;
            margin: 1rem 0;
        }
        
        .footer-links li {
            margin: 0 1rem;
        }
        
        .footer-links a {
            color: white;
            text-decoration: none;
        }
        
        .footer-links a:hover {
            text-decoration: underline;
        }
        
        .copyright {
            margin-top: 1rem;
            font-size: 0.9rem;
            color: #bdc3c7;
        }
        
        /* Responsive */
        @media (max-width: 768px) {
            .nav-links {
                display: none;
            }
            
            .hero h1 {
                font-size: 2rem;
            }
            
            .hero p {
                font-size: 1rem;
            }
            
            .btn-container {
                flex-direction: column;
                align-items: center;
            }
            
            .btn {
                width: 80%;
                margin-bottom: 1rem;
                text-align: center;
            }
        }
    </style>
</head>
<body>
    <header>
        <div class="container">
            <nav>
                <div class="logo">Pulmoscan.ai</div>
                <ul class="nav-links">
                    <li><a href="/">Home</a></li>
                    <li><a href="/features">Features</a></li>
                    <li><a href="/contact">Contact</a></li>
                </ul>
            </nav>
        </div>
    </header>

    <section class="hero">
        <div class="container">
            <h1>Revolutionizing TB Diagnosis & Care</h1>
            <p>Pulmoscan.ai combines advanced AI technology with medical expertise to provide accurate TB diagnosis, personalized treatment plans, and comprehensive patient care.</p>
            <div class="btn-container">
                <a href="/login?type=patient" class="btn">Patient Login</a>
                <a href="/login?type=healthcare" class="btn btn-secondary">Healthcare Worker Login</a>
            </div>
        </div>
    </section>

    <section class="features">
        <div class="container">
            <h2>Our Key Features</h2>
            <div class="feature-grid">
                <div class="feature-card">
                    <div class="feature-icon">
                        <i class="fas fa-lungs"></i>
                    </div>
                    <h3>X-Ray Analysis</h3>
                    <p>Advanced AI-powered analysis of chest X-rays to detect TB with high accuracy.</p>
                </div>
                <div class="feature-card">
                    <div class="feature-icon">
                        <i class="fas fa-pills"></i>
                    </div>
                    <h3>Treatment Guidance</h3>
                    <p>Personalized treatment plans based on patient data and medical history.</p>
                </div>
                <div class="feature-card">
                    <div class="feature-icon">
                        <i class="fas fa-user-md"></i>
                    </div>
                    <h3>Telemedicine</h3>
                    <p>Connect with healthcare professionals remotely for consultations and follow-ups.</p>
                </div>
            </div>
            <a href="/features" class="btn" style="margin-top: 2rem;">Explore All Features</a>
        </div>
    </section>

    <footer>
        <div class="container">
            <div class="footer-content">
                <div class="logo">Pulmoscan.ai</div>
            <ul class="footer-links">
                <li><a href="/">Home</a></li>
                <li><a href="/features">Features</a></li>
                <li><a href="/contact">Contact</a></li>
            </ul>
                <p class="copyright">&copy; 2025 Pulmoscan.ai. All rights reserved.</p>
            </div>
        </div>
    </footer>
    
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            const patientBtn = document.querySelector('.btn-container .btn');
            const healthcareBtn = document.querySelector('.btn-container .btn-secondary');
            
            patientBtn.addEventListener('click', function(e) {
                e.preventDefault();
                window.location.href = '/login?type=patient';
            });
            
            healthcareBtn.addEventListener('click', function(e) {
                e.preventDefault();
                window.location.href = '/login?type=healthcare';
            });
        });
    </script>
</body>
</html>
        '''
    elif template_name == 'features':
        return '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Features - Pulmoscan.ai</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        /* Global Styles */
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        
        body {
            background-color: #f5f7fa;
            color: #333;
            line-height: 1.6;
        }
        
        .container {
            width: 90%;
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 20px;
        }
        
        /* Header Styles */
        header {
            background-color: #2c3e50;
            color: white;
            padding: 1rem 0;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        
        nav {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .logo {
            font-size: 1.8rem;
            font-weight: bold;
            color: #3498db;
        }
        
        .nav-links {
            display: flex;
            list-style: none;
        }
        
        .nav-links li {
            margin-left: 2rem;
        }
        
        .nav-links a {
            color: white;
            text-decoration: none;
            font-weight: 500;
            transition: color 0.3s;
        }
        
        .nav-links a:hover {
            color: #3498db;
        }
        
        /* Page Header */
        .page-header {
            background: linear-gradient(rgba(44, 62, 80, 0.8), rgba(44, 62, 80, 0.8)), url('https://placehold.co/1200x400');
            background-size: cover;
            background-position: center;
            color: white;
            text-align: center;
            padding: 3rem 0;
            margin-bottom: 3rem;
        }
        
        .page-header h1 {
            font-size: 2.5rem;
            margin-bottom: 1rem;
        }
        
        .page-header p {
            font-size: 1.1rem;
            max-width: 800px;
            margin: 0 auto;
        }
        
        /* Features Section */
        .features-list {
            padding: 2rem 0;
        }
        
        .feature-item {
            display: flex;
            align-items: center;
            background-color: white;
            border-radius: 10px;
            padding: 2rem;
            margin-bottom: 2rem;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        
        .feature-icon {
            font-size: 3rem;
            color: #3498db;
            margin-right: 2rem;
            min-width: 80px;
            text-align: center;
        }
        
        .feature-content h2 {
            font-size: 1.8rem;
            margin-bottom: 1rem;
            color: #2c3e50;
        }
        
        .feature-content p {
            color: #7f8c8d;
            margin-bottom: 1rem;
        }
        
        /* CTA Section */
        .cta {
            background-color: #3498db;
            color: white;
            text-align: center;
            padding: 3rem 0;
            margin: 3rem 0;
            border-radius: 10px;
        }
        
        .cta h2 {
            font-size: 2rem;
            margin-bottom: 1rem;
        }
        
        .cta p {
            font-size: 1.1rem;
            max-width: 800px;
            margin: 0 auto 2rem;
        }
        
        .btn {
            display: inline-block;
            background-color: white;
            color: #3498db;
            padding: 0.8rem 2rem;
            border-radius: 5px;
            text-decoration: none;
            font-weight: 600;
            transition: background-color 0.3s;
        }
        
        .btn:hover {
            background-color: #f5f5f5;
        }
        
        /* Footer */
        footer {
            background-color: #2c3e50;
            color: white;
            padding: 2rem 0;
            text-align: center;
        }
        
        .footer-content {
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        
        .footer-links {
            display: flex;
            list-style: none;
            margin: 1rem 0;
        }
        
        .footer-links li {
            margin: 0 1rem;
        }
        
        .footer-links a {
            color: white;
            text-decoration: none;
        }
        
        .footer-links a:hover {
            text-decoration: underline;
        }
        
        .copyright {
            margin-top: 1rem;
            font-size: 0.9rem;
            color: #bdc3c7;
        }
        
        /* Responsive */
        @media (max-width: 768px) {
            .nav-links {
                display: none;
            }
            
            .feature-item {
                flex-direction: column;
                text-align: center;
            }
            
            .feature-icon {
                margin-right: 0;
                margin-bottom: 1rem;
            }
        }
    </style>
</head>
<body>
    <header>
        <div class="container">
            <nav>
                <div class="logo">Pulmoscan.ai</div>
                <ul class="nav-links">
                    <li><a href="/">Home</a></li>
                    <li><a href="/features">Features</a></li>
                    <li><a href="/contact">Contact</a></li>
                </ul>
            </nav>
        </div>
    </header>

    <section class="page-header">
        <div class="container">
            <h1>Our Comprehensive Features</h1>
            <p>Discover how Pulmoscan.ai is transforming TB diagnosis and care with cutting-edge AI technology and patient-centered solutions.</p>
        </div>
    </section>

    <section class="features-list">
        <div class="container">
            <div class="feature-item">
                <div class="feature-icon">
                    <i class="fas fa-lungs"></i>
                </div>
                <div class="feature-content">
                    <h2>X-Ray Analysis & Highlighting</h2>
                    <p>Our advanced CAD4TB technology analyzes chest X-rays to detect TB with high accuracy. The system assigns probability scores indicating the likelihood of TB and differentiates between active infections and scarring.</p>
                    <p>Healthcare workers receive detailed reports with highlighted areas of concern, making diagnosis faster and more accurate.</p>
                </div>
            </div>

            <div class="feature-item">
                <div class="feature-icon">
                    <i class="fas fa-pills"></i>
                </div>
                <div class="feature-content">
                    <h2>Personalized Treatment Guidance</h2>
                    <p>Our AI models predict patient responses to specific TB treatments based on clinical and genomic data. This allows healthcare workers to tailor treatment regimens for both drug-susceptible and drug-resistant TB.</p>
                    <p>The system recommends optimal treatment plans, including dosage adjustments for individual patients based on weight, age, symptoms, and genomic resistance patterns.</p>
                </div>
            </div>

            <div class="feature-item">
                <div class="feature-icon">
                    <i class="fas fa-book-medical"></i>
                </div>
                <div class="feature-content">
                    <h2>Patient Education & Precautions</h2>
                    <p>Comprehensive educational resources explain TB, its transmission, symptoms, and prevention measures through multimedia formats (text, videos, infographics).</p>
                    <p>Interactive modules teach patients about medication adherence and necessary lifestyle changes to support recovery and prevent transmission.</p>
                </div>
            </div>

            <div class="feature-item">
                <div class="feature-icon">
                    <i class="fas fa-calendar-check"></i>
                </div>
                <div class="feature-content">
                    <h2>Medication Adherence Tracking</h2>
                    <p>Patients can log their medication intake within the app and receive timely reminders through push notifications to ensure consistent adherence to treatment protocols.</p>
                    <p>Visual progress tracking shows treatment milestones, motivating patients to complete their full course of medication.</p>
                </div>
            </div>

            <div class="feature-item">
                <div class="feature-icon">
                    <i class="fas fa-exclamation-triangle"></i>
                </div>
                <div class="feature-content">
                    <h2>Side Effect Monitoring</h2>
                    <p>Self-reporting tools allow patients to document potential side effects of medications directly within the app.</p>
                    <p>The system suggests remedies for minor side effects and alerts healthcare providers if severe side effects are reported, ensuring timely intervention.</p>
                </div>
            </div>

            <div class="feature-item">
                <div class="feature-icon">
                    <i class="fas fa-video"></i>
                </div>
                <div class="feature-content">
                    <h2>Telemedicine Integration</h2>
                    <p>Remote consultations connect patients with healthcare providers, especially beneficial for those in remote areas with limited access to TB specialists.</p>
                    <p>AI-powered clinical decision support systems assist doctors in providing recommendations remotely, improving the quality of virtual care.</p>
                </div>
            </div>

            <div class="feature-item">
                <div class="feature-icon">
                    <i class="fas fa-dna"></i>
                </div>
                <div class="feature-content">
                    <h2>Advanced Diagnostics</h2>
                    <p>Genomic analysis of Mycobacterium tuberculosis strains helps identify drug resistance patterns, enabling tailored treatments for multidrug-resistant TB (MDR-TB) or extensively drug-resistant TB (XDR-TB).</p>
                    <p>Integration with laboratory systems allows for comprehensive diagnostic reporting in one centralized platform.</p>
                </div>
            </div>

            <div class="feature-item">
                <div class="feature-icon">
                    <i class="fas fa-users"></i>
                </div>
                <div class="feature-content">
                    <h2>Community Screening Tools</h2>
                    <p>Risk factor screening questionnaires help community health workers identify high-risk individuals who should be prioritized for TB testing.</p>
                    <p>Smartphone-based tools, such as cough analysis via microphones, provide preliminary screening capabilities in resource-limited settings.</p>
                </div>
            </div>

            <div class="feature-item">
                <div class="feature-icon">
                    <i class="fas fa-wifi-slash"></i>
                </div>
                <div class="feature-content">
                    <h2>Offline Functionality</h2>
                    <p>All essential features work offline by leveraging edge AI technologies, making the platform accessible in areas with limited internet connectivity.</p>
                    <p>Patient data is stored securely on local devices until internet connectivity is available for synchronization, ensuring continuity of care.</p>
                </div>
            </div>
        </div>
    </section>

    <section class="cta">
        <div class="container">
            <h2>Ready to Transform TB Care?</h2>
            <p>Join thousands of healthcare professionals and patients who are already benefiting from Pulmoscan.ai's innovative approach to TB diagnosis and treatment.</p>
            <a href="/register" class="btn">Get Started Today</a>
        </div>
    </section>

    <footer>
        <div class="container">
            <div class="footer-content">
                <div class="logo">Pulmoscan.ai</div>
            <ul class="footer-links">
                <li><a href="/">Home</a></li>
                <li><a href="/features">Features</a></li>
                <li><a href="/contact">Contact</a></li>
            </ul>
                <p class="copyright">&copy; 2025 Pulmoscan.ai. All rights reserved.</p>
            </div>
        </div>
    </footer>
</body>
</html>
        '''
    elif template_name == 'contact':
        return '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Contact Us - Pulmoscan.ai</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        /* Global Styles */
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        
        body {
            background-color: #f5f7fa;
            color: #333;
            line-height: 1.6;
        }
        
        .container {
            width: 90%;
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 20px;
        }
        
        /* Header Styles */
        header {
            background-color: #2c3e50;
            color: white;
            padding: 1rem 0;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        
        nav {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .logo {
            font-size: 1.8rem;
            font-weight: bold;
            color: #3498db;
        }
        
        .nav-links {
            display: flex;
            list-style: none;
        }
        
        .nav-links li {
            margin-left: 2rem;
        }
        
        .nav-links a {
            color: white;
            text-decoration: none;
            font-weight: 500;
            transition: color 0.3s;
        }
        
        .nav-links a:hover {
            color: #3498db;
        }
        
        /* Page Header */
        .page-header {
            background: linear-gradient(rgba(44, 62, 80, 0.8), rgba(44, 62, 80, 0.8)), url('https://placehold.co/1200x400');
            background-size: cover;
            background-position: center;
            color: white;
            text-align: center;
            padding: 3rem 0;
            margin-bottom: 3rem;
        }
        
        .page-header h1 {
            font-size: 2.5rem;
            margin-bottom: 1rem;
        }
        
        .page-header p {
            font-size: 1.1rem;
            max-width: 800px;
            margin: 0 auto;
        }
        
        /* Contact Section */
        .contact-section {
            display: flex;
            flex-wrap: wrap;
            gap: 2rem;
            margin-bottom: 3rem;
        }
        
        .contact-form {
            flex: 1;
            min-width: 300px;
            background-color: white;
            padding: 2rem;
            border-radius: 10px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        
        .contact-form h2 {
            font-size: 1.8rem;
            margin-bottom: 1.5rem;
            color: #2c3e50;
        }
        
        .form-group {
            margin-bottom: 1.5rem;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 0.5rem;
            font-weight: 600;
            color: #2c3e50;
        }
        
        .form-group input,
        .form-group textarea {
            width: 100%;
            padding: 0.8rem;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 1rem;
        }
        
        .form-group textarea {
            min-height: 150px;
            resize: vertical;
        }
        
        .btn {
            display: inline-block;
            background-color: #3498db;
            color: white;
            padding: 0.8rem 2rem;
            border-radius: 5px;
            text-decoration: none;
            font-weight: 600;
            transition: background-color 0.3s;
            border: none;
            cursor: pointer;
        }
        
        .btn:hover {
            background-color: #2980b9;
        }
        
        .contact-info {
            flex: 1;
            min-width: 300px;
        }
        
        .info-card {
            background-color: white;
            padding: 2rem;
            border-radius: 10px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            margin-bottom: 2rem;
        }
        
        .info-card h2 {
            font-size: 1.8rem;
            margin-bottom: 1.5rem;
            color: #2c3e50;
        }
        
        .info-item {
            display: flex;
            align-items: flex-start;
            margin-bottom: 1.5rem;
        }
        
        .info-icon {
            font-size: 1.5rem;
            color: #3498db;
            margin-right: 1rem;
            min-width: 30px;
        }
        
        .info-content h3 {
            font-size: 1.2rem;
            margin-bottom: 0.5rem;
            color: #2c3e50;
        }
        
        .info-content p {
            color: #7f8c8d;
        }
        
        .social-links {
            display: flex;
            gap: 1rem;
            margin-top: 1rem;
        }
        
        .social-links a {
            display: inline-block;
            width: 40px;
            height: 40px;
            background-color: #3498db;
            color: white;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: background-color 0.3s;
        }
        
        .social-links a:hover {
            background-color: #2980b9;
        }
        
        /* Map Section */
        .map-section {
            margin-bottom: 3rem;
        }
        
        .map-container {
            height: 400px;
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        
        /* Footer */
        footer {
            background-color: #2c3e50;
            color: white;
            padding: 2rem 0;
            text-align: center;
        }
        
        .footer-content {
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        
        .footer-links {
            display: flex;
            list-style: none;
            margin: 1rem 0;
        }
        
        .footer-links li {
            margin: 0 1rem;
        }
        
        .footer-links a {
            color: white;
            text-decoration: none;
        }
        
        .footer-links a:hover {
            text-decoration: underline;
        }
        
        .copyright {
            margin-top: 1rem;
            font-size: 0.9rem;
            color: #bdc3c7;
        }
        
        /* Alert Messages */
        .alert {
            padding: 1rem;
            border-radius: 5px;
            margin-bottom: 1rem;
        }
        
        .alert-success {
            background-color: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        
        .alert-danger {
            background-color: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        
        /* Responsive */
        @media (max-width: 768px) {
            .nav-links {
                display: none;
            }
            
            .contact-section {
                flex-direction: column;
            }
        }
    </style>
</head>
<body>
    <header>
        <div class="container">
            <nav>
                <div class="logo">Pulmoscan.ai</div>
                <ul class="nav-links">
                    <li><a href="/">Home</a></li>
                    <li><a href="/features">Features</a></li>
                    <li><a href="/contact">Contact</a></li>
                </ul>
            </nav>
        </div>
    </header>

    <section class="page-header">
        <div class="container">
            <h1>Contact Us</h1>
            <p>Have questions about Pulmoscan.ai? We're here to help. Reach out to our team for support, inquiries, or partnership opportunities.</p>
        </div>
    </section>

    <section class="container">
        <div class="contact-section">
            <div class="contact-form">
                <h2>Send Us a Message</h2>
                <form id="contactForm" action="/submit_contact" method="POST">
                    <div class="form-group">
                        <label for="name">Your Name</label>
                        <input type="text" id="name" name="name" required>
            </div>
                    <div class="form-group">
                        <label for="email">Email Address</label>
                        <input type="email" id="email" name="email" required>
                    </div>
                    <div class="form-group">
                        <label for="message">Message</label>
                        <textarea id="message" name="message" required></textarea>
                    </div>
                    <button type="submit" class="btn">Send Message</button>
                </form>
            </div>
            
                <div class="contact-info">
                <div class="info-card">
                    <h2>Contact Information</h2>
                    <div class="info-item">
                        <div class="info-icon">
                            <i class="fas fa-map-marker-alt"></i>
                        </div>
                        <div class="info-content">
                            <h3>Our Location</h3>
                            <p>123 Innovation Drive, Health Tech Park, Bangalore, India</p>
                        </div>
                    </div>
                    <div class="info-item">
                        <div class="info-icon">
                            <i class="fas fa-phone-alt"></i>
                        </div>
                        <div class="info-content">
                            <h3>Phone Number</h3>
                            <p>+91 80 1234 5678</p>
                    </div>
                        </div>
                    <div class="info-item">
                        <div class="info-icon">
                            <i class="fas fa-envelope"></i>
                    </div>
                        <div class="info-content">
                            <h3>Email Address</h3>
                            <p>info@pulmoscan.ai</p>
                        </div>
                    </div>
                    <div class="info-item">
                        <div class="info-icon">
                            <i class="fas fa-clock"></i>
                        </div>
                        <div class="info-content">
                            <h3>Working Hours</h3>
                            <p>Monday - Friday: 9:00 AM - 6:00 PM</p>
                        </div>
                    </div>
                    <div class="social-links">
                        <a href="#"><i class="fab fa-facebook-f"></i></a>
                        <a href="#"><i class="fab fa-twitter"></i></a>
                        <a href="#"><i class="fab fa-linkedin-in"></i></a>
                        <a href="#"><i class="fab fa-instagram"></i></a>
                </div>
                        </div>
                        </div>
                        </div>
                        
        <div class="map-section">
            <div class="map-container">
                <iframe src="https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d497699.9973874144!2d77.35073573336324!3d12.95384772557775!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x3bae1670c9b44e6d%3A0xf8dfc3e8517e4fe0!2sBengaluru%2C%20Karnataka%2C%20India!5e0!3m2!1sen!2sus!4v1650450351910!5m2!1sen!2sus" width="100%" height="100%" style="border:0;" allowfullscreen="" loading="lazy"></iframe>
            </div>
        </div>
    </section>

    <footer>
        <div class="container">
            <div class="footer-content">
                <div class="logo">Pulmoscan.ai</div>
            <ul class="footer-links">
                <li><a href="/">Home</a></li>
                <li><a href="/features">Features</a></li>
                <li><a href="/contact">Contact</a></li>
            </ul>
                <p class="copyright">&copy; 2025 Pulmoscan.ai. All rights reserved.</p>
            </div>
        </div>
    </footer>
    
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            const contactForm = document.getElementById('contactForm');
            
            contactForm.addEventListener('submit', function(e) {
                e.preventDefault();
                
                const formData = new FormData(contactForm);
                
                fetch('/submit_contact', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        alert('Thank you for your message! We will get back to you soon.');
                        contactForm.reset();
                    } else {
                        alert('There was an error sending your message. Please try again.');
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('There was an error sending your message. Please try again.');
                });
            });
        });
    </script>
</body>
</html>
        '''
    elif template_name == 'login':
        return '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login - Pulmoscan.ai</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        /* Global Styles */
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }
        
        body {
            background-color: #f5f7fa;
            color: #333;
            line-height: 1.6;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }
        
        .container {
            width: 90%;
            max-width: 1200px;
            margin: 0 auto;
            padding: 0 20px;
        }
        
        /* Header Styles */
        header {
            background-color: #2c3e50;
            color: white;
            padding: 1rem 0;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        
        nav {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .logo {
            font-size: 1.8rem;
            font-weight: bold;
            color: #3498db;
        }
        
        .nav-links {
            display: flex;
            list-style: none;
        }
        
        .nav-links li {
            margin-left: 2rem;
        }
        
        .nav-links a {
            color: white;
            text-decoration: none;
            font-weight: 500;
            transition: color 0.3s;
        }
        
        .nav-links a:hover {
            color: #3498db;
        }
        
        /* Main Content */
        main {
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 3rem 0;
        }
        
        .auth-container {
            width: 100%;
            max-width: 400px;
            background-color: white;
            border-radius: 10px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            padding: 2rem;
        }
        
        .auth-header {
            text-align: center;
            margin-bottom: 2.auth-header h2 {
            font-size: 2rem;
            color: #2c3e50;
            margin-bottom: 0.5rem;
        }
        
        .auth-header p {
            color: #7f8c8d;
            margin-bottom: 2rem;
        }
        
        .form-group {
            margin-bottom: 1.5rem;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 0.5rem;
            font-weight: 600;
            color: #2c3e50;
        }
        
        .form-group input {
            width: 100%;
            padding: 0.8rem;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 1rem;
        }
        
        .form-group input:focus {
            outline: none;
            border-color: #3498db;
        }
        
        .btn {
            display: block;
            width: 100%;
            background-color: #3498db;
            color: white;
            padding: 0.8rem;
            border: none;
            border-radius: 5px;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: background-color 0.3s;
        }
        
        .btn:hover {
            background-color: #2980b9;
        }
        
        .auth-footer {
            text-align: center;
            margin-top: 2rem;
            padding-top: 1rem;
            border-top: 1px solid #ddd;
        }
        
        .auth-footer p {
            color: #7f8c8d;
            margin-bottom: 1rem;
        }
        
        .auth-footer a {
            color: #3498db;
            text-decoration: none;
            font-weight: 600;
        }
        
        .auth-footer a:hover {
            text-decoration: underline;
        }
        
        /* Alert Messages */
        .alert {
            padding: 1rem;
            border-radius: 5px;
            margin-bottom: 1rem;
            text-align: center;
        }
        
        .alert-danger {
            background-color: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        
        /* Footer */
        footer {
            background-color: #2c3e50;
            color: white;
            padding: 2rem 0;
            text-align: center;
            margin-top: auto;
        }
        
        .footer-content {
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        
        .footer-links {
            display: flex;
            list-style: none;
            margin: 1rem 0;
        }
        
        .footer-links li {
            margin: 0 1rem;
        }
        
        .footer-links a {
            color: white;
            text-decoration: none;
        }
        
        .footer-links a:hover {
            text-decoration: underline;
        }
        
        .copyright {
            margin-top: 1rem;
            font-size: 0.9rem;
            color: #bdc3c7;
        }
        
        /* Responsive */
        @media (max-width: 768px) {
            .nav-links {
                display: none;
            }
            
            .auth-container {
                margin: 0 1rem;
            }
        }
    </style>
</head>
<body>
    <header>
        <div class="container">
            <nav>
                <div class="logo">Pulmoscan.ai</div>
                <ul class="nav-links">
                    <li><a href="/">Home</a></li>
                    <li><a href="/features">Features</a></li>
                    <li><a href="/contact">Contact</a></li>
                </ul>
            </nav>
        </div>
    </header>

    <main>
            <div class="auth-container">
                <div class="auth-header">
                <h2>Welcome Back</h2>
                <p>Login to access your dashboard</p>
                </div>
                
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="alert alert-{{ category }}">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            <form id="loginForm" action="/login" method="POST">
                    <div class="form-group">
                        <label for="username">Username</label>
                    <input type="text" id="username" name="username" required>
                    </div>
                    <div class="form-group">
                        <label for="password">Password</label>
                    <input type="password" id="password" name="password" required>
                    </div>
                <button type="submit" class="btn">Login</button>
                </form>
                
                <div class="auth-footer">
                <p>Don't have an account? <a href="/register">Register Now</a></p>
                </div>
            </div>
    </main>
    
    <footer>
        <div class="container">
            <div class="footer-content">
                <div class="logo">Pulmoscan.ai</div>
                <ul class="footer-links">
                    <li><a href="/">Home</a></li>
                    <li><a href="/features">Features</a></li>
                    <li><a href="/contact">Contact</a></li>
                </ul>
                <p class="copyright">&copy; 2025 Pulmoscan.ai. All rights reserved.</p>
        </div>
        </div>
    </footer>

    <script>
        document.addEventListener('DOMContentLoaded', function() {
            const loginForm = document.getElementById('loginForm');
            
            loginForm.addEventListener('submit', function(e) {
                e.preventDefault();
                
                const formData = new FormData(loginForm);
                
                fetch('/login', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        window.location.href = data.redirect;
                    } else {
                        const alert = document.createElement('div');
                        alert.className = 'alert alert-danger';
                        alert.textContent = data.message;
                        loginForm.insertBefore(alert, loginForm.firstChild);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    const alert = document.createElement('div');
                    alert.className = 'alert alert-danger';
                    alert.textContent = 'An error occurred. Please try again.';
                    loginForm.insertBefore(alert, loginForm.firstChild);
                });
            });
        });
    </script>
</body>
</html>
'''

# Register template
@app.route('/register_template')
def register_template():
    return '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Register - Pulmoscan.ai</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        /* [Previous CSS styles remain the same] */
        
        /* Additional styles for registration form */
        .user-type-selector {
            display: flex;
            gap: 1rem;
            margin-bottom: 1.5rem;
        }
        
        .user-type-option {
            flex: 1;
            text-align: center;
            padding: 1rem;
            border: 2px solid #ddd;
            border-radius: 5px;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .user-type-option:hover {
            border-color: #3498db;
        }
        
        .user-type-option.selected {
            border-color: #3498db;
            background-color: #ebf5fb;
        }
        
        .user-type-option i {
            font-size: 2rem;
            color: #3498db;
            margin-bottom: 0.5rem;
        }
        
        .terms-checkbox {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            margin-bottom: 1.5rem;
        }
        
        .terms-checkbox input[type="checkbox"] {
            width: auto;
        }
    </style>
</head>
<body>
    <header>
        <div class="container">
            <nav>
                <div class="logo">Pulmoscan.ai</div>
                <ul class="nav-links">
                    <li><a href="/">Home</a></li>
                    <li><a href="/features">Features</a></li>
                    <li><a href="/contact">Contact</a></li>
                </ul>
            </nav>
        </div>
    </header>

    <main>
            <div class="auth-container">
            <div class="auth-header">
                <h2>Create Account</h2>
                <p>Join Pulmoscan.ai to access our services</p>
                </div>
            
            <form id="registerForm" action="/register" method="POST">
                <div class="user-type-selector">
                    <div class="user-type-option" data-type="patient">
                        <i class="fas fa-user"></i>
                        <h3>Patient</h3>
                </div>
                    <div class="user-type-option" data-type="healthcare">
                        <i class="fas fa-user-md"></i>
                        <h3>Healthcare Worker</h3>
                    </div>
                </div>
                <input type="hidden" name="user_type" id="userType" required>
                
                <div class="form-group">
                    <label for="name">Full Name</label>
                    <input type="text" id="name" name="name" required>
                </div>
                
                <div class="form-group">
                    <label for="email">Email Address</label>
                    <input type="email" id="email" name="email" required>
                </div>
                
                    <div class="form-group">
                        <label for="username">Username</label>
                    <input type="text" id="username" name="username" required>
                    </div>
                    
                    <div class="form-group">
                        <label for="password">Password</label>
                    <input type="password" id="password" name="password" required>
                    </div>
                    
                <div class="terms-checkbox">
                    <input type="checkbox" id="terms" name="terms" required>
                    <label for="terms">I agree to the Terms & Conditions and Privacy Policy</label>
                    </div>
                    
                <button type="submit" class="btn">Create Account</button>
                </form>
                
                <div class="auth-footer">
                <p>Already have an account? <a href="/login">Login</a></p>
                </div>
            </div>
    </main>
    
    <footer>
        <div class="container">
            <div class="footer-content">
                <div class="logo">Pulmoscan.ai</div>
                <ul class="footer-links">
                    <li><a href="/">Home</a></li>
                    <li><a href="/features">Features</a></li>
                    <li><a href="/contact">Contact</a></li>
                </ul>
                <p class="copyright">&copy; 2025 Pulmoscan.ai. All rights reserved.</p>
        </div>
        </div>
    </footer>
    
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            const userTypeOptions = document.querySelectorAll('.user-type-option');
            const userTypeInput = document.getElementById('userType');
            const registerForm = document.getElementById('registerForm');
            
            userTypeOptions.forEach(option => {
                option.addEventListener('click', function() {
                    userTypeOptions.forEach(opt => opt.classList.remove('selected'));
                    this.classList.add('selected');
                    userTypeInput.value = this.dataset.type;
                });
            });
            
            registerForm.addEventListener('submit', function(e) {
                e.preventDefault();
                
                if (!userTypeInput.value) {
                    alert('Please select a user type');
                    return;
                }
                
                const formData = new FormData(registerForm);
                
                fetch('/register', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        window.location.href = data.redirect;
                    } else {
                        const alert = document.createElement('div');
                        alert.className = 'alert alert-danger';
                        alert.textContent = data.message;
                        registerForm.insertBefore(alert, registerForm.firstChild);
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    const alert = document.createElement('div');
                    alert.className = 'alert alert-danger';
                    alert.textContent = 'An error occurred. Please try again.';
                    registerForm.insertBefore(alert, registerForm.firstChild);
                });
            });
        });
    </script>
</body>
</html>
'''

# Patient Dashboard template
@app.route('/patient_dashboard_template')
def patient_dashboard_template():
    return '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Patient Dashboard - Pulmoscan.ai</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        /* [Previous CSS styles remain the same] */
        
        /* Dashboard specific styles */
        .dashboard {
            display: flex;
            min-height: calc(100vh - 60px);
        }
        
        .sidebar {
            width: 250px;
            background-color: #2c3e50;
            color: white;
            padding: 2rem 0;
        }
        
        .sidebar-menu {
            list-style: none;
        }
        
        .sidebar-menu li {
            margin-bottom: 0.5rem;
        }
        
        .sidebar-menu a {
            display: flex;
            align-items: center;
            padding: 1rem 2rem;
            color: white;
            text-decoration: none;
            transition: background-color 0.3s;
        }
        
        .sidebar-menu a:hover {
            background-color: #34495e;
        }
        
        .sidebar-menu i {
            margin-right: 1rem;
        }
        
        .main-content {
            flex: 1;
            padding: 2rem;
            background-color: #f5f7fa;
        }
        
        .dashboard-header {
            margin-bottom: 2rem;
        }
        
        .dashboard-header h1 {
            font-size: 2rem;
            color: #2c3e50;
            margin-bottom: 0.5rem;
        }
        
        .upload-section {
            background-color: white;
            border-radius: 10px;
            padding: 2rem;
            margin-bottom: 2rem;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        
        .upload-section h2 {
            font-size: 1.5rem;
            color: #2c3e50;
            margin-bottom: 1.5rem;
        }
        
        .upload-options {
            display: flex;
            gap: 2rem;
            margin-bottom: 2rem;
        }
        
        .upload-option {
            flex: 1;
            text-align: center;
            padding: 2rem;
            border: 2px dashed #ddd;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .upload-option:hover {
            border-color: #3498db;
            background-color: #ebf5fb;
        }
        
        .upload-option i {
            font-size: 3rem;
            color: #3498db;
            margin-bottom: 1rem;
        }
        
        .symptoms-form {
            display: none;
        }
        
        .symptoms-form.active {
            display: block;
        }
        
        .form-group {
            margin-bottom: 1.5rem;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 0.5rem;
            font-weight: 600;
            color: #2c3e50;
        }
        
        .radio-group {
            display: flex;
            gap: 2rem;
        }
        
        .radio-option {
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        
        .analysis-section {
            background-color: white;
            border-radius: 10px;
            padding: 2rem;
            margin-bottom: 2rem;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        
        .analysis-section h2 {
            font-size: 1.5rem;
            color: #2c3e50;
            margin-bottom: 1.5rem;
        }
        
        .analysis-results {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 2rem;
        }
        
        .result-card {
            background-color: #f8f9fa;
            border-radius: 10px;
            padding: 1.5rem;
        }
        
        .result-card h3 {
            font-size: 1.2rem;
            color: #2c3e50;
            margin-bottom: 1rem;
        }
        
        .probability-meter {
            height: 20px;
            background-color: #ddd;
            border-radius: 10px;
            overflow: hidden;
            margin-bottom: 1rem;
        }
        
        .probability-fill {
            height: 100%;
            background-color: #3498db;
            transition: width 0.3s;
        }
        
        .medications-section {
            background-color: white;
            border-radius: 10px;
            padding: 2rem;
            margin-bottom: 2rem;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        
        .medication-list {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 1.5rem;
        }
        
        .medication-card {
            background-color: #f8f9fa;
            border-radius: 10px;
            padding: 1.5rem;
        }
        
        .medication-card h3 {
            font-size: 1.2rem;
            color: #2c3e50;
            margin-bottom: 0.5rem;
        }
        
        .medication-card p {
            color: #7f8c8d;
            margin-bottom: 0.5rem;
        }
        
        .prevention-section {
            background-color: white;
            border-radius: 10px;
            padding: 2rem;
            margin-bottom: 2rem;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        
        .prevention-steps {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 1.5rem;
        }
        
        .prevention-card {
            background-color: #f8f9fa;
            border-radius: 10px;
            padding: 1.5rem;
            text-align: center;
        }
        
        .prevention-card i {
            font-size: 2.5rem;
            color: #3498db;
            margin-bottom: 1rem;
        }
        
        .prevention-card h3 {
            font-size: 1.2rem;
            color: #2c3e50;
            margin-bottom: 0.5rem;
        }
        
        .guidance-section {
            background-color: white;
            border-radius: 10px;
            padding: 2rem;
            margin-bottom: 2rem;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        
        .language-selector {
            margin-bottom: 1.5rem;
        }
        
        .language-selector select {
            padding: 0.5rem;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 1rem;
        }
        
        .guidance-content {
            margin-bottom: 2rem;
        }
        
        .video-container {
            position: relative;
            padding-bottom: 56.25%;
            height: 0;
            overflow: hidden;
        }
        
        .video-container iframe {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
        }
    </style>
</head>
<body>
    <div class="dashboard">
        <aside class="sidebar">
            <ul class="sidebar-menu">
                <li>
                    <a href="#upload">
                        <i class="fas fa-upload"></i>
                        Upload Reports
                    </a>
                </li>
                <li>
                    <a href="#analysis">
                        <i class="fas fa-chart-bar"></i>
                        Analysis
                    </a>
                </li>
                <li>
                    <a href="#medications">
                        <i class="fas fa-pills"></i>
                        Medications
                    </a>
                </li>
                <li>
                    <a href="#prevention">
                        <i class="fas fa-shield-alt"></i>
                        Prevention Steps
                    </a>
                </li>
                <li>
                    <a href="#guidance">
                        <i class="fas fa-book-medical"></i>
                        TB Guidance
                    </a>
                </li>
                <li>
                    <a href="/logout">
                        <i class="fas fa-sign-out-alt"></i>
                        Logout
                    </a>
                </li>
            </ul>
        </aside>
        
        <main class="main-content">
            <div class="dashboard-header">
                <h1>Welcome, {{ patient.name }}</h1>
                <p>Manage your TB diagnosis and treatment</p>
            </div>
            
            <section id="upload" class="upload-section">
                <h2>Upload Reports</h2>
                <div class="upload-options">
                    <div class="upload-option" id="xrayUpload">
                        <i class="fas fa-x-ray"></i>
                        <h3>Upload X-Ray</h3>
                        <p>Click to upload chest X-ray image</p>
                        <input type="file" hidden accept="image/*">
                    </div>
                    <div class="upload-option" id="sputumUpload">
                        <i class="fas fa-vial"></i>
                        <h3>Sputum Test</h3>
                        <p>Enter sputum test results</p>
                    </div>
            </div>
            
                <form class="symptoms-form" id="symptomsForm">
                    <h3>Symptoms Questionnaire</h3>
                    <div class="form-group">
                        <label>Do you have a persistent cough?</label>
                        <div class="radio-group">
                            <div class="radio-option">
                                <input type="radio" name="symptom_cough" value="yes" id="coughYes">
                                <label for="coughYes">Yes</label>
                    </div>
                            <div class="radio-option">
                                <input type="radio" name="symptom_cough" value="no" id="coughNo">
                                <label for="coughNo">No</label>
                            </div>
                    </div>
                </div>
                
                    <div class="form-group">
                        <label>Have you experienced chest pain?</label>
                        <div class="radio-group">
                            <div class="radio-option">
                                <input type="radio" name="symptom_chest_pain" value="yes" id="chestPainYes">
                                <label for="chestPainYes">Yes</label>
                    </div>
                            <div class="radio-option">
                                <input type="radio" name="symptom_chest_pain" value="no" id="chestPainNo">
                                <label for="chestPainNo">No</label>
                            </div>
                    </div>
                </div>
                
                    <div class="form-group">
                        <label>Have you had night sweats?</label>
                        <div class="radio-group">
                            <div class="radio-option">
                                <input type="radio" name="symptom_night_sweats" value="yes" id="sweatsYes">
                                <label for="sweatsYes">Yes</label>
                    </div>
                            <div class="radio-option">
                                <input type="radio" name="symptom_night_sweats" value="no" id="sweatsNo">
                                <label for="sweatsNo">No</label>
                    </div>
                </div>
            </div>
            
                    <button type="submit" class="btn">Submit</button>
                </form>
            </section>
            
            <section id="analysis" class="analysis-section">
                <h2>Analysis Results</h2>
                <div class="analysis-results">
                    <div class="result-card">
                        <h3>X-Ray Analysis</h3>
                        <div class="probability-meter">
                            <div class="probability-fill" style="width: 75%;"></div>
                        </div>
                        <p>TB Probability: 75%</p>
                        <p>Areas of concern detected in upper right lobe</p>
                </div>
                
                    <div class="result-card">
                        <h3>Sputum Test Results</h3>
                        <div class="probability-meter">
                            <div class="probability-fill" style="width: 65%;"></div>
                    </div>
                        <p>TB Probability: 65%</p>
                        <p>Moderate bacterial presence detected</p>
                    </div>
                </div>
            </section>
            
            <section id="medications" class="medications-section">
                <h2>Current Medications</h2>
                <div class="medication-list">
                    <div class="medication-card">
                        <h3>Isoniazid</h3>
                        <p>Dosage: 300mg daily</p>
                        <p>Duration: 6 months</p>
                        <p>Next dose in: 2 hours</p>
                    </div>
                    
                    <div class="medication-card">
                        <h3>Rifampin</h3>
                        <p>Dosage: 600mg daily</p>
                        <p>Duration: 6 months</p>
                        <p>Next dose in: 2 hours</p>
                    </div>
                    
                    <div class="medication-card">
                        <h3>Ethambutol</h3>
                        <p>Dosage: 15mg/kg daily</p>
                        <p>Duration: 2 months</p>
                        <p>Next dose in: 2 hours</p>
                </div>
                </div>
            </section>
            
            <section id="prevention" class="prevention-section">
                <h2>Prevention Steps</h2>
                <div class="prevention-steps">
                    <div class="prevention-card">
                        <i class="fas fa-head-side-mask"></i>
                        <h3>Cover Your Mouth</h3>
                        <p>Always cover when coughing or sneezing</p>
                        </div>
                        
                    <div class="prevention-card">
                        <i class="fas fa-wind"></i>
                        <h3>Ventilate Rooms</h3>
                        <p>Ensure good ventilation in all rooms</p>
                        </div>
                        
                    <div class="prevention-card">
                        <i class="fas fa-pills"></i>
                        <h3>Complete Treatment</h3>
                        <p>Take all medications as prescribed</p>
                </div>
                </div>
            </section>
            
            <section id="guidance" class="guidance-section">
                <h2>TB Guidance</h2>
                <div class="language-selector">
                    <select id="languageSelect">
                        <option value="english">English</option>
                        <option value="kannada">ಕನ್ನಡ</option>
                        <option value="telugu">తెలుగు</option>
                        <option value="tamil">தமிழ்</option>
                        <option value="malayalam">മലയാളം</option>
                            </select>
                        </div>
                        
                <div class="guidance-content">
                    <h3>Understanding Tuberculosis</h3>
                    <p>Tuberculosis (TB) is an infectious disease that usually affects the lungs...</p>
                        </div>
                        
                <div class="video-container">
                    <iframe src="https://www.youtube.com/embed/example" frameborder="0" allowfullscreen></iframe>
                </div>
            </section>
        </main>
                </div>
                
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            // Upload functionality
            const xrayUpload = document.getElementById('xrayUpload');
            const sputumUpload = document.getElementById('sputumUpload');
            const symptomsForm = document.getElementById('symptomsForm');
            const xrayInput = xrayUpload.querySelector('input[type="file"]');
            
            xrayUpload.addEventListener('click', () => xrayInput.click());
            
            xrayInput.addEventListener('change', function() {
                if (this.files && this.files[0])if (this.files && this.files[0]) {
                    const formData = new FormData();
                    formData.append('xray', this.files[0]);
                    
                    fetch('/upload_report', {
                        method: 'POST',
                        body: formData
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            updateAnalysisResults(data.analysis);
                            alert('X-ray uploaded successfully!');
                        } else {
                            alert('Error uploading X-ray. Please try again.');
                        }
                    })
                    .catch(error => {
                        console.error('Error:', error);
                        alert('An error occurred. Please try again.');
                    });
                }
            });
            
            sputumUpload.addEventListener('click', function() {
                symptomsForm.classList.add('active');
            });
            
            symptomsForm.addEventListener('submit', function(e) {
                e.preventDefault();
                
                const formData = new FormData(symptomsForm);
                
                fetch('/upload_report', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        updateAnalysisResults(data.analysis);
                        symptomsForm.classList.remove('active');
                        symptomsForm.reset();
                        alert('Symptoms submitted successfully!');
                    } else {
                        alert('Error submitting symptoms. Please try again.');
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    alert('An error occurred. Please try again.');
                });
            });
            
            // Language selector functionality
            const languageSelect = document.getElementById('languageSelect');
            const guidanceContent = document.querySelector('.guidance-content');
            const videoContainer = document.querySelector('.video-container iframe');
            
            languageSelect.addEventListener('change', function() {
                fetch(`/get_tb_guidance?language=${this.value}`)
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            const guidance = data.guidance;
                            guidanceContent.innerHTML = `
                                <h3>${guidance.title}</h3>
                                <p>${guidance.content}</p>
                            `;
                            videoContainer.src = guidance.video_url;
                        }
                    })
                    .catch(error => console.error('Error:', error));
            });
            
            // Update analysis results
            function updateAnalysisResults(analysis) {
                const analysisResults = document.querySelector('.analysis-results');
                let html = '';
                
                if (analysis.xray) {
                    html += `
                        <div class="result-card">
                            <h3>X-Ray Analysis</h3>
                            <div class="probability-meter">
                                <div class="probability-fill" style="width: ${analysis.xray.tb_probability * 100}%;"></div>
                            </div>
                            <p>TB Probability: ${(analysis.xray.tb_probability * 100).toFixed(1)}%</p>
                            <p>${analysis.xray.recommendation}</p>
                        </div>
                    `;
                }
                
                if (analysis.sputum) {
                    html += `
                        <div class="result-card">
                            <h3>Sputum Test Results</h3>
                            <div class="probability-meter">
                                <div class="probability-fill" style="width: ${analysis.sputum.tb_probability * 100}%;"></div>
                </div>
                            <p>TB Probability: ${(analysis.sputum.tb_probability * 100).toFixed(1)}%</p>
                            <p>${analysis.sputum.recommendation}</p>
            </div>
                    `;
                }
                
                analysisResults.innerHTML = html;
            }
            
            // Load medications
            function loadMedications() {
                fetch('/get_medications')
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            const medicationList = document.querySelector('.medication-list');
                            let html = '';
                            
                            data.medications.forEach(med => {
                                html += `
                                    <div class="medication-card">
                                        <h3>${med.name}</h3>
                                        <p>Dosage: ${med.dosage}</p>
                                        <p>Duration: ${med.duration}</p>
                                    </div>
                                `;
                            });
                            
                            medicationList.innerHTML = html;
                        }
                    })
                    .catch(error => console.error('Error:', error));
            }
            
            // Load prevention steps
            function loadPreventionSteps() {
                fetch('/get_prevention_steps')
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            const preventionSteps = document.querySelector('.prevention-steps');
                            let html = '';
                            
                            data.prevention_steps.forEach(step => {
                                html += `
                                    <div class="prevention-card">
                                        <i class="fas ${step.icon}"></i>
                                        <h3>${step.title}</h3>
                                        <p>${step.description}</p>
                                    </div>
                                `;
                            });
                            
                            preventionSteps.innerHTML = html;
                        }
                    })
                    .catch(error => console.error('Error:', error));
            }
            
            // Initialize dashboard
            loadMedications();
            loadPreventionSteps();
            
            // Sidebar navigation
            const sidebarLinks = document.querySelectorAll('.sidebar-menu a');
            
            sidebarLinks.forEach(link => {
                link.addEventListener('click', function(e) {
                    if (this.getAttribute('href').startsWith('#')) {
                        e.preventDefault();
                        const targetId = this.getAttribute('href').substring(1);
                        const targetSection = document.getElementById(targetId);
                        targetSection.scrollIntoView({ behavior: 'smooth' });
                    }
                });
            });
        });
    </script>
</body>
</html>
'''

# Healthcare Worker Dashboard template
@app.route('/healthcare_dashboard_template')
def healthcare_dashboard_template():
    return '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Healthcare Worker Dashboard - Pulmoscan.ai</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        /* [Previous CSS styles remain the same] */
        
        /* Healthcare Dashboard specific styles */
        .patient-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 2rem;
        }
        
        .patient-card {
            background-color: white;
            border-radius: 10px;
            padding: 1.5rem;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        
        .patient-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
        }
        
        .patient-name {
            font-size: 1.2rem;
            font-weight: 600;
            color: #2c3e50;
        }
        
        .patient-status {
            padding: 0.3rem 0.8rem;
            border-radius: 20px;
            font-size: 0.9rem;
            font-weight: 500;
        }
        
        .status-pending {
            background-color: #fff3cd;
            color: #856404;
        }
        
        .status-accepted {
            background-color: #d4edda;
            color: #155724;
        }
        
        .status-rejected {
            background-color: #f8d7da;
            color: #721c24;
        }
        
        .patient-info {
            margin-bottom: 1rem;
        }
        
        .info-item {
            display: flex;
            align-items: center;
            margin-bottom: 0.5rem;
        }
        
        .info-item i {
            margin-right: 0.5rem;
            color: #3498db;
        }
        
        .patient-actions {
            display: flex;
            gap: 1rem;
        }
        
        .action-btn {
            flex: 1;
            padding: 0.5rem;
            border: none;
            border-radius: 5px;
            font-weight: 500;
            cursor: pointer;
            transition: background-color 0.3s;
        }
        
        .btn-accept {
            background-color: #28a745;
            color: white;
        }
        
        .btn-accept:hover {
            background-color: #218838;
        }
        
        .btn-reject {
            background-color: #dc3545;
            color: white;
        }
        
        .btn-reject:hover {
            background-color: #c82333;
        }
        
        .btn-view {
            background-color: #3498db;
            color: white;
        }
        
        .btn-view:hover {
            background-color: #2980b9;
        }
        
        .patient-reports {
            margin-top: 1rem;
        }
        
        .report-item {
            display: flex;
            align-items: center;
            padding: 0.5rem;
            background-color: #f8f9fa;
            border-radius: 5px;
            margin-bottom: 0.5rem;
        }
        
        .report-icon {
            margin-right: 0.5rem;
            color: #3498db;
        }
        
        .report-info {
            flex: 1;
        }
        
        .report-actions {
            display: flex;
            gap: 0.5rem;
        }
        
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0,0,0,0.5);
            z-index: 1000;
        }
        
        .modal-content {
            position: relative;
            background-color: white;
            width: 90%;
            max-width: 800px;
            margin: 2rem auto;
            padding: 2rem;
            border-radius: 10px;
            max-height: 90vh;
            overflow-y: auto;
        }
        
        .close-modal {
            position: absolute;
            top: 1rem;
            right: 1rem;
            font-size: 1.5rem;
            cursor: pointer;
        }
        
        .report-viewer {
            margin-top: 1rem;
        }
        
        .report-image {
            width: 100%;
            max-height: 500px;
            object-fit: contain;
            margin-bottom: 1rem;
        }
        
        .report-analysis {
            background-color: #f8f9fa;
            padding: 1rem;
            border-radius: 5px;
        }
        
        .treatment-form {
            margin-top: 1rem;
        }
        
        .treatment-form textarea {
            width: 100%;
            min-height: 100px;
            margin-bottom: 1rem;
            padding: 0.5rem;
            border: 1px solid #ddd;
            border-radius: 5px;
        }
    </style>
</head>
<body>
    <div class="dashboard">
        <aside class="sidebar">
            <ul class="sidebar-menu">
                <li>
                    <a href="#patient-records">
                        <i class="fas fa-users"></i>
                        Patient Records
                    </a>
                </li>
                <li>
                    <a href="#my-patients">
                        <i class="fas fa-user-check"></i>
                        My Patients
                    </a>
                </li>
                <li>
                    <a href="#cured-patients">
                        <i class="fas fa-heart"></i>
                        Cured Patients
                    </a>
                </li>
                <li>
                    <a href="/logout">
                        <i class="fas fa-sign-out-alt"></i>
                        Logout
                    </a>
                </li>
            </ul>
        </aside>
        
        <main class="main-content">
            <div class="dashboard-header">
                <h1>Welcome, Dr. {{ healthcare_worker.name }}</h1>
                <p>Manage your patients and their treatment plans</p>
            </div>
            
            <section id="patient-records">
                <h2>Patient Records</h2>
                <div class="patient-grid">
                    {% for record in patient_records.values() %}
                    <div class="patient-card">
                        <div class="patient-header">
                            <span class="patient-name">{{ record.patient_name }}</span>
                            <span class="patient-status status-{{ record.status }}">{{ record.status|title }}</span>
            </div>
                        <div class="patient-info">
                            <div class="info-item">
                                <i class="fas fa-calendar"></i>
                                <span>{{ record.date }}</span>
                    </div>
                            {% if record.xray %}
                            <div class="info-item">
                                <i class="fas fa-x-ray"></i>
                                <span>X-Ray Available</span>
                    </div>
                            {% endif %}
                            {% if record.sputum %}
                            <div class="info-item">
                                <i class="fas fa-vial"></i>
                                <span>Sputum Test Available</span>
                </div>
                            {% endif %}
                    </div>
                        <div class="patient-actions">
                            {% if record.status == 'pending' %}
                            <button class="action-btn btn-accept" onclick="acceptPatient('{{ record.id }}')">Accept</button>
                            <button class="action-btn btn-reject" onclick="rejectPatient('{{ record.id }}')">Reject</button>
                            {% endif %}
                            <button class="action-btn btn-view" onclick="viewPatientReport('{{ record.id }}')">View Report</button>
                    </div>
                </div>
                            {% endfor %}
                </div>
            </section>
            
            <section id="my-patients">
                    <h2>My Patients</h2>
                <div class="patient-grid">
                    {% for patient in accepted_patients.values() %}
                    <div class="patient-card">
                        <div class="patient-header">
                            <span class="patient-name">{{ patient.name }}</span>
                            <span class="patient-status status-accepted">Active</span>
                        </div>
                        <div class="patient-info">
                            <div class="info-item">
                                <i class="fas fa-calendar"></i>
                                <span>Started: {{ patient.start_date }}</span>
                            </div>
                            <div class="info-item">
                                <i class="fas fa-pills"></i>
                                <span>Current Treatment: {{ patient.current_treatment }}</span>
                            </div>
                                </div>
                        <div class="patient-reports">
                            {% for report in patient.reports %}
                            <div class="report-item">
                                <i class="fas fa-file-medical report-icon"></i>
                                <div class="report-info">
                                    <div>{{ report.type }} - {{ report.date }}</div>
                                    <div>Status: {{ report.status }}</div>
                            </div>
                                <div class="report-actions">
                                    <button class="action-btn btn-view" onclick="viewReport('{{ report.id }}')">View</button>
                        </div>
                    </div>
                    {% endfor %}
                </div>
                        <div class="patient-actions">
                            <button class="action-btn btn-view" onclick="viewPatientHistory('{{ patient.id }}')">View History</button>
                            <button class="action-btn btn-accept" onclick="markAsCured('{{ patient.id }}')">Mark as Cured</button>
            </div>
                    </div>
                            {% endfor %}
                </div>
            </section>
            
            <section id="cured-patients">
                <h2>Cured Patients</h2>
                <div class="patient-grid">
                    {% for patient in cured_patients.values() %}
                    <div class="patient-card">
                        <div class="patient-header">
                            <span class="patient-name">{{ patient.name }}</span>
                            <span class="patient-status status-accepted">Cured</span>
        </div>
                        <div class="patient-info">
                            <div class="info-item">
                                <i class="fas fa-calendar"></i>
                                <span>Treatment Duration: {{ patient.treatment_duration }}</span>
                    </div>
                            <div class="info-item">
                                <i class="fas fa-check-circle"></i>
                                <span>Cured Date: {{ patient.cure_date }}</span>
                    </div>
                </div>
                        <div class="patient-actions">
                            <button class="action-btn btn-view" onclick="viewPatientHistory('{{ patient.id }}')">View History</button>
                    </div>
                            </div>
                                {% endfor %}
                            </div>
            </section>
        </main>
                        </div>
                        
    <!-- Report Viewer Modal -->
    <div id="reportModal" class="modal">
        <div class="modal-content">
            <span class="close-modal">&times;</span>
            <h2>Patient Report</h2>
            <div class="report-viewer">
                <img src="" alt="X-Ray" class="report-image" id="reportImage">
                <div class="report-analysis" id="reportAnalysis"></div>
                <form class="treatment-form" id="treatmentForm">
                    <h3>Treatment Recommendations</h3>
                    <textarea name="treatment" placeholder="Enter treatment recommendations..."></textarea>
                    <button type="submit" class="btn">Save Recommendations</button>
                </form>
                        </div>
                        </div>
                    </div>
                    
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            const modal = document.getElementById('reportModal');
            const closeModal = document.querySelector('.close-modal');
            const treatmentForm = document.getElementById('treatmentForm');
            
            closeModal.addEventListener('click', () => {
                modal.style.display = 'none';
            });
            
            window.addEventListener('click', (e) => {
                if (e.target === modal) {
                    modal.style.display = 'none';
                }
            });
            
            // View patient report
            window.viewPatientReport = function(reportId) {
                fetch(`/get_analysis/${reportId}`)
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            const reportImage = document.getElementById('reportImage');
                            const reportAnalysis = document.getElementById('reportAnalysis');
                            
                            reportImage.src = data.analysis.xray_image;
                            reportAnalysis.innerHTML = `
                                <h3>Analysis Results</h3>
                                <p>TB Probability: ${(data.analysis.xray.tb_probability * 100).toFixed(1)}%</p>
                                <p>Recommendation: ${data.analysis.xray.recommendation}</p>
                            `;
                            
                            modal.style.display = 'block';
                        }
                    })
                    .catch(error => console.error('Error:', error));
            };
            
            // Accept patient
            window.acceptPatient = function(reportId) {
                fetch('/accept_patient', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                    },
                    body: `report_id=${reportId}`
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        alert('Patient accepted successfully!');
                        location.reload();
                    } else {
                        alert('Error accepting patient. Please try again.');
                    }
                })
                .catch(error => console.error('Error:', error));
            };
            
            // Reject patient
            window.rejectPatient = function(reportId) {
                fetch('/reject_patient', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                    },
                    body: `report_id=${reportId}`
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        alert('Patient rejected.');
                        location.reload();
                    } else {
                        alert('Error rejecting patient. Please try again.');
                    }
                })
                .catch(error => console.error('Error:', error));
            };
            
            // Mark patient as cured
            window.markAsCured = function(patientId) {
                fetch('/mark_cured', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                    },
                    body: `patient_username=${patientId}`
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        alert('Patient marked as cured!');
                        location.reload();
                    } else {
                        alert('Error marking patient as cured. Please try again.');
                    }
                })
                .catch(error => console.error('Error:', error));
            };
            
            // Treatment form submission
            treatmentForm.addEventListener('submit', function(e) {
                e.preventDefault();
                
                const formData = new FormData(treatmentForm);
                
                fetch('/save_treatment', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        alert('Treatment recommendations saved successfully!');
                        modal.style.display = 'none';
                    } else {
                        alert('Error saving treatment recommendations. Please try again.');
                    }
                })
                .catch(error => console.error('Error:', error));
            });
            
            // Sidebar navigation
            const sidebarLinks = document.querySelectorAll('.sidebar-menu a');
            
            sidebarLinks.forEach(link => {
                link.addEventListener('click', function(e) {
                    if (this.getAttribute('href').startsWith('#')) {
                        e.preventDefault();
                        const targetId = this.getAttribute('href').substring(1);
                        const targetSection = document.getElementById(targetId);
                        targetSection.scrollIntoView({ behavior: 'smooth' });
                    }
                });
            });
        });
    </script>
</body>
</html>
'''

def process_xray_and_highlight(image_data):
    # Convert image bytes to numpy array
    nparr = np.frombuffer(image_data, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    # Convert to grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # Apply CLAHE with increased contrast
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
    enhanced = clahe.apply(gray)
    
    # Apply bilateral filter to reduce noise while preserving edges
    blurred = cv2.bilateralFilter(enhanced, 9, 75, 75)
    
    # Create a mask for potential TB regions
    _, thresh1 = cv2.threshold(blurred, 127, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, thresh2 = cv2.threshold(blurred, 150, 255, cv2.THRESH_BINARY)
    
    # Combine thresholds for better detection
    thresh = cv2.bitwise_or(thresh1, thresh2)
    
    # Apply morphological operations to enhance regions
    kernel = np.ones((5,5), np.uint8)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
    
    # Find contours with hierarchy
    contours, hierarchy = cv2.findContours(thresh, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    
    # Create heatmap overlay
    heatmap = np.zeros_like(gray)
    
    # Filter contours and calculate areas
    significant_contours = []
    total_area = 0
    max_area = img.shape[0] * img.shape[1]
    
    for contour in contours:
        area = cv2.contourArea(contour)
        if area > 200:  # Increased minimum area threshold
            significant_contours.append(contour)
            total_area += area
    
    area_ratio = min(total_area / max_area * 5, 1.0)  # Increased sensitivity
    
    infected_areas = []
    # Create more prominent heatmap
    for contour in significant_contours:
        # Draw filled contour with high intensity
        cv2.drawContours(heatmap, [contour], -1, 255, -1)
        
        # Calculate centroid
        M = cv2.moments(contour)
        if M["m00"] != 0:
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])
            
            # Calculate relative coordinates
            rel_x = (cx / img.shape[1]) * 100
            rel_y = (cy / img.shape[0]) * 100
            
            # Calculate local intensity using original enhanced image
            mask = np.zeros_like(gray)
            cv2.drawContours(mask, [contour], -1, 255, -1)
            local_intensity = cv2.mean(enhanced, mask=mask)[0] / 255.0
            
            # Store infected area info
            infected_areas.append({
                'x': float(rel_x),
                'y': float(rel_y),
                'area': float(cv2.contourArea(contour)),
                'severity': float(max(0.6, local_intensity))  # Minimum severity of 0.6
            })
    
    # Apply a more prominent color map
    heatmap_colored = cv2.applyColorMap(heatmap, cv2.COLORMAP_JET)
    
    # Enhance the contrast of the heatmap
    heatmap_colored = cv2.convertScaleAbs(heatmap_colored, alpha=1.5, beta=0)
    
    # Create more visible overlay
    highlighted = cv2.addWeighted(img, 0.6, heatmap_colored, 0.4, 0)
    
    # Add a colored border around detected regions
    for contour in significant_contours:
        cv2.drawContours(highlighted, [contour], -1, (0, 255, 255), 2)
    
    # Convert the highlighted image to base64
    _, buffer = cv2.imencode('.jpg', highlighted)
    img_base64 = base64.b64encode(buffer).decode('utf-8')
    
    # Calculate TB probability with adjusted weights
    num_regions = len(infected_areas)
    avg_severity = np.mean([area['severity'] for area in infected_areas]) if infected_areas else 0
    
    # Enhanced probability calculation
    area_weight = 0.35
    severity_weight = 0.35
    regions_weight = 0.30
    
    base_probability = (
        area_weight * area_ratio +
        severity_weight * avg_severity +
        regions_weight * min(num_regions / 8, 1.0)  # Adjusted region cap
    )
    
    # Ensure minimum probability of 0.5 for detected regions
    tb_probability = max(0.5, base_probability) if infected_areas else 0.3
    
    # Generate findings based on analysis
    findings = []
    if tb_probability > 0.7:
        findings.append({
            'disease': 'Severe Infiltration',
            'probability': float(tb_probability),
            'severity': 'High'
        })
    elif tb_probability > 0.5:
        findings.append({
            'disease': 'Moderate Infiltration',
            'probability': float(tb_probability),
            'severity': 'Moderate'
        })
    
    if num_regions > 3:
        findings.append({
            'disease': 'Multiple Lesions',
            'probability': float(min(num_regions / 8, 0.9)),
            'severity': 'High' if num_regions > 6 else 'Moderate'
        })
    
    if avg_severity > 0.6:
        findings.append({
            'disease': 'Dense Opacity',
            'probability': float(avg_severity),
            'severity': 'High' if avg_severity > 0.8 else 'Moderate'
        })
    
    # Ensure we always return some findings
    if not findings:
        findings.append({
            'disease': 'Potential Abnormality',
            'probability': float(tb_probability),
            'severity': 'Low'
        })
    
    return {
        'image_base64': img_base64,
        'infected_areas': infected_areas,
        'tb_probability': float(tb_probability),
        'findings': findings,
        'confidence_score': float(min(0.95, 0.7 + tb_probability * 0.25))
    }

if __name__ == '__main__':
    # Enable Jinja2 template caching
    app.jinja_env.cache = {}
    
    # Run the app with optimized settings
    app.run(debug=True, threaded=True)