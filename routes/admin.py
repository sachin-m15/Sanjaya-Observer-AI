from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, session, Response
from models.database import (
    get_supabase_client, get_children, get_observers, get_parents,
    save_observation, get_observer_children, upload_file_to_storage, get_signed_audio_url,
    # Multi-tenant functions
    get_organizations, create_organization, get_pending_observer_applications,
    review_observer_application, get_users_by_organization, get_organization_by_id,
    auto_assign_parent_to_organization, get_children_by_organization,
    get_observer_child_mappings_by_organization
)
from models.observation_extractor import ObservationExtractor
from utils.decorators import admin_required
import pandas as pd
import uuid
import json
from datetime import datetime
import io
import re
import urllib.parse
import logging
import os

# Set up logging to replace print statements
logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__)


@admin_bp.route('/dashboard')
@admin_required
def dashboard():
    # Get comprehensive analytics data for the dashboard
    try:
        supabase = get_supabase_client()

        # Get comprehensive analytics data
        users_response = supabase.table('users').select("id", count="exact").execute()
        observers_response = supabase.table('users').select("id", count="exact").eq('role', 'Observer').execute()
        parents_response = supabase.table('users').select("id", count="exact").eq('role', 'Parent').execute()
        principals_response = supabase.table('users').select("id", count="exact").eq('role', 'Principal').execute()
        children_response = supabase.table('children').select("id", count="exact").execute()
        organizations_response = supabase.table('organizations').select("id", count="exact").execute()

        # Get pending applications for BOTH observer and principal
        pending_observer_apps = supabase.table('observer_applications').select("id", count="exact").eq('status', 'pending').execute()
        pending_principal_apps = supabase.table('principal_applications').select("id", count="exact").eq('status', 'pending').execute()

        # Get all reports with detailed information including file URLs
        all_reports_response = supabase.table('observations').select("""
            id, student_name, observer_name, date, timestamp, filename, 
            file_url, full_data, processed_by_admin, username, student_id
        """).order('timestamp', desc=True).execute()

        all_reports = all_reports_response.data if all_reports_response.data else []

        # Process reports to extract formatted reports and file info
        processed_reports = []
        for report in all_reports:
            processed_report = {
                'id': report.get('id'),
                'student_name': report.get('student_name', 'N/A'),
                'observer_name': report.get('observer_name', 'N/A'),
                'date': report.get('date', 'N/A'),
                'timestamp': report.get('timestamp', 'N/A'),
                'filename': report.get('filename', 'N/A'),
                'file_url': report.get('file_url'),
                'processed_by_admin': report.get('processed_by_admin', False),
                'has_formatted_report': False,
                'formatted_report': None,
                'file_type': None,
                'signed_url': None,
                'organization_name': 'N/A'
            }

            # URL encode the file_url to handle spaces and special characters
            if processed_report['file_url']:
                processed_report['file_url'] = urllib.parse.quote(processed_report['file_url'],
                                                                  safe=':/?#[]@!$&\'()*+,;=')

                # Determine file type from URL or filename
                file_url_lower = processed_report['file_url'].lower()
                if any(ext in file_url_lower for ext in ['.mp3', '.wav', '.m4a', '.ogg']):
                    processed_report['file_type'] = 'audio'
                    filename = processed_report['file_url'].split('/')[-1]
                    signed_url = get_signed_audio_url(filename)
                    if signed_url:
                        processed_report['signed_url'] = signed_url
                elif any(ext in file_url_lower for ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp']):
                    processed_report['file_type'] = 'image'

            # Extract formatted report from full_data
            if report.get('full_data'):
                try:
                    full_data = json.loads(report['full_data'])
                    if full_data.get('formatted_report'):
                        processed_report['has_formatted_report'] = True
                        processed_report['formatted_report'] = "Report Available"
                except:
                    pass

            processed_reports.append(processed_report)

        # Get recent activity (last 10 observations)
        recent_observations = processed_reports[:10]

        # Get system status data
        total_storage_files = len([r for r in processed_reports if r['file_url']])

        # Get organizations for dropdown
        organizations = get_organizations()

        analytics = {
            'total_users': users_response.count if users_response.count else 0,
            'observers_count': observers_response.count if observers_response.count else 0,
            'parents_count': parents_response.count if parents_response.count else 0,
            'principals_count': principals_response.count if principals_response.count else 0,
            'children_count': children_response.count if children_response.count else 0,
            'observations_count': len(all_reports),
            'organizations_count': organizations_response.count if organizations_response.count else 0,
            'pending_observer_applications': pending_observer_apps.count if pending_observer_apps.count else 0,
            'pending_principal_applications': pending_principal_apps.count if pending_principal_apps.count else 0,
            'storage_files': total_storage_files,
            'recent_observations': recent_observations,
            'all_reports': processed_reports,
            'organizations': organizations
        }

        # Add stats for both applications
        stats = {
            'pending_observer': pending_observer_apps.count if pending_observer_apps.count else 0,
            'pending_principal': pending_principal_apps.count if pending_principal_apps.count else 0
        }

        logger.info(f"Admin dashboard loaded with {analytics['total_users']} users and {analytics['organizations_count']} organizations")

    except Exception as e:
        logger.error(f"Error in admin dashboard: {e}")
        analytics = {
            'total_users': 0, 'observers_count': 0, 'parents_count': 0, 'principals_count': 0,
            'children_count': 0, 'observations_count': 0, 'organizations_count': 0,
            'pending_observer_applications': 0, 'pending_principal_applications': 0, 'storage_files': 0,
            'recent_observations': [], 'all_reports': [], 'organizations': []
        }
        stats = {'pending_observer': 0, 'pending_principal': 0}

    return render_template('admin/dashboard.html', analytics=analytics, stats=stats)


@admin_bp.route('/view_report/<report_id>')
@admin_required
def view_report(report_id):
    """View specific report"""
    try:
        supabase = get_supabase_client()

        # Get the specific report
        report_response = supabase.table('observations').select("*").eq('id', report_id).execute()

        if not report_response.data:
            flash('Report not found', 'error')
            return redirect(url_for('admin.dashboard'))

        report = report_response.data[0]

        # URL encode the file_url if it exists and create signed URL for audio
        if report.get('file_url'):
            report['file_url'] = urllib.parse.quote(report['file_url'], safe=':/?#[]@!$&\'()*+,;=')

            # If it's an audio file, create signed URL
            if any(ext in report['file_url'].lower() for ext in ['.mp3', '.wav', '.m4a', '.ogg']):
                filename = report['file_url'].split('/')[-1]
                signed_url = get_signed_audio_url(filename)
                if signed_url:
                    report['signed_url'] = signed_url

        # Extract formatted report from full_data
        formatted_report = None
        if report.get('full_data'):
            try:
                full_data = json.loads(report['full_data'])
                formatted_report = full_data.get('formatted_report')
            except:
                pass

        return render_template('admin/view_report.html',
                             report=report,
                             formatted_report=formatted_report)

    except Exception as e:
        flash(f'Error loading report: {str(e)}', 'error')
        return redirect(url_for('admin.dashboard'))


@admin_bp.route('/generate_transcript/<report_id>')
@admin_required
def generate_transcript(report_id):
    """Generate conversational transcript from audio/text observations"""
    try:
        supabase = get_supabase_client()

        # Get the specific report
        report_response = supabase.table('observations').select("*").eq('id', report_id).execute()

        if not report_response.data:
            return jsonify({'success': False, 'error': 'Report not found'})

        report = report_response.data[0]

        # Get the raw observations/transcript
        raw_text = report.get('observations', '')
        if not raw_text:
            return jsonify({'success': False, 'error': 'No transcript data available'})

        # Generate conversational format using Gemini API
        extractor = ObservationExtractor()
        conversational_transcript = extractor.generate_conversational_transcript(raw_text)

        return jsonify({
            'success': True,
            'transcript': conversational_transcript,
            'student_name': report.get('student_name', 'Unknown'),
            'observer_name': report.get('observer_name', 'Unknown'),
            'date': report.get('date', 'Unknown')
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@admin_bp.route('/download_report/<report_id>')
@admin_required
def download_report(report_id):
    try:
        supabase = get_supabase_client()
        report_data = supabase.table('observations').select("*").eq('id', report_id).execute().data

        if not report_data:
            flash('Report not found', 'error')
            return redirect(url_for('admin.dashboard'))

        report = report_data[0]

        # Get formatted report from full_data
        formatted_report = None
        if report.get('full_data'):
            try:
                full_data = json.loads(report['full_data'])
                formatted_report = full_data.get('formatted_report')
            except:
                pass

        if not formatted_report:
            flash('No formatted report available', 'error')
            return redirect(url_for('admin.dashboard'))

        # Create Word document
        extractor = ObservationExtractor()
        doc_buffer = extractor.create_word_document_with_emojis(formatted_report)

        # Create filename
        student_name = report['student_name']
        if student_name:
            clean_name = re.sub(r'[^\w\s-]', '', student_name).strip()
            clean_name = re.sub(r'[-\s]+', '_', clean_name)
        else:
            clean_name = 'Student'

        date = report['date'] if report['date'] else datetime.now().strftime('%Y-%m-%d')
        filename = f"report_{clean_name}_{date}.docx"

        return send_file(
            doc_buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )

    except Exception as e:
        flash(f'Error downloading report: {str(e)}', 'error')
        return redirect(url_for('admin.dashboard'))


@admin_bp.route('/user_management')
@admin_required
def user_management():
    """User management with organization assignment capabilities"""
    try:
        # Get all data
        users = get_observers() + get_parents()  # Get all users
        children = get_children()
        organizations = get_organizations()

        # Add principals to users list
        supabase = get_supabase_client()
        principals_response = supabase.table('users').select("*").eq('role', 'Principal').execute()
        if principals_response.data:
            users.extend(principals_response.data)

        # Get all users for complete list
        all_users_response = supabase.table('users').select("*").execute()
        all_users = all_users_response.data if all_users_response.data else []

        return render_template('admin/user_management.html',
                               users=all_users,
                               children=children,
                               organizations=organizations)
    except Exception as e:
        logger.error(f"Error loading user management: {str(e)}")
        flash(f'Error loading user management: {str(e)}', 'error')
        return render_template('admin/user_management.html',
                               users=[], children=[], organizations=[])


@admin_bp.route('/add_user', methods=['POST'])
@admin_required
def add_user():
    """Add single user - handles all user types including children"""
    try:
        name = request.form.get('name')
        email = request.form.get('email')
        role = request.form.get('role')
        password = request.form.get('password')
        organization_id = request.form.get('organization_id')
        child_id = request.form.get('child_id') if role == 'Parent' else None

        # Validation
        if not all([name, role]):
            flash('Name and role are required.', 'error')
            return redirect(url_for('admin.user_management'))

        supabase = get_supabase_client()

        # Handle Child creation (goes to children table)
        if role == 'Child':
            child_data = {
                "id": str(uuid.uuid4()),
                "name": name,
                "birth_date": request.form.get('birth_date'),
                "grade": request.form.get('grade'),
                "organization_id": organization_id,
                "created_at": datetime.now().isoformat()
            }

            result = supabase.table('children').insert(child_data).execute()
            if result.data:
                flash('Child added successfully!', 'success')
            else:
                flash('Error adding child.', 'error')

        # Handle User creation (goes to users table)
        else:
            if not email or not password:
                flash('Email and password are required for user accounts.', 'error')
                return redirect(url_for('admin.user_management'))

            user_data = {
                "id": str(uuid.uuid4()),
                "email": email.strip().lower(),
                "name": name,
                "password": password,
                "role": role,
                "organization_id": organization_id,
                "created_at": datetime.now().isoformat()
            }

            if child_id:
                user_data["child_id"] = child_id

            result = supabase.table('users').insert(user_data).execute()
            if result.data:
                flash(f'{role} added successfully!', 'success')
            else:
                flash(f'Error adding {role.lower()}.', 'error')

    except Exception as e:
        logger.error(f'Error adding user: {str(e)}')
        flash(f'Error adding user: {str(e)}', 'error')

    return redirect(url_for('admin.user_management'))


@admin_bp.route('/bulk_upload_users', methods=['POST'])
@admin_required
def bulk_upload_users():
    """Bulk upload users via CSV"""
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('admin.user_management'))

    file = request.files['file']
    upload_type = request.form.get('upload_type')
    organization_id = request.form.get('bulk_organization_id')

    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('admin.user_management'))

    try:
        df = pd.read_csv(file)
        supabase = get_supabase_client()

        if upload_type == 'children':
            if 'name' not in df.columns:
                flash('CSV must contain a "name" column', 'error')
                return redirect(url_for('admin.user_management'))

            children_data = []
            for _, row in df.iterrows():
                child_data = {
                    "id": str(uuid.uuid4()),
                    "name": row['name'],
                    "birth_date": row.get('birth_date', None) if pd.notna(row.get('birth_date')) else None,
                    "grade": row.get('grade', None) if pd.notna(row.get('grade')) else None,
                    "organization_id": organization_id,
                    "created_at": datetime.now().isoformat()
                }
                children_data.append(child_data)

            batch_size = 50
            for i in range(0, len(children_data), batch_size):
                batch = children_data[i:i + batch_size]
                supabase.table('children').insert(batch).execute()

            flash(f'Successfully added {len(children_data)} children!', 'success')

        elif upload_type in ['parents', 'observers', 'principals']:
            required_cols = ['name', 'email', 'password']
            if not all(col in df.columns for col in required_cols):
                flash('CSV must contain "name", "email", and "password" columns', 'error')
                return redirect(url_for('admin.user_management'))

            users_data = []
            for _, row in df.iterrows():
                user_data = {
                    "id": str(uuid.uuid4()),
                    "name": row['name'],
                    "email": row['email'].strip().lower(),
                    "password": row['password'],
                    "role": upload_type[:-1].capitalize(),  # 'parents' -> 'Parent'
                    "organization_id": organization_id,
                    "created_at": datetime.now().isoformat()
                }
                users_data.append(user_data)

            batch_size = 50
            for i in range(0, len(users_data), batch_size):
                batch = users_data[i:i + batch_size]
                supabase.table('users').insert(batch).execute()

            flash(f'Successfully added {len(users_data)} {upload_type}!', 'success')

    except Exception as e:
        logger.error(f'Error processing bulk upload: {str(e)}')
        flash(f'Error processing file: {str(e)}', 'error')

    return redirect(url_for('admin.user_management'))


@admin_bp.route('/mappings')
@admin_required
def mappings():
    try:
        observers = get_observers()
        children = get_children()
        parents = get_parents()
        supabase = get_supabase_client()
        observer_mappings = supabase.table('observer_child_mappings').select("*").execute().data

        return render_template('admin/mappings.html',
                               observers=observers,
                               children=children,
                               parents=parents,
                               observer_mappings=observer_mappings)
    except Exception as e:
        flash(f'Error loading mappings: {str(e)}', 'error')
        return render_template('admin/mappings.html',
                               observers=[],
                               children=[],
                               parents=[],
                               observer_mappings=[])


@admin_bp.route('/add_mapping', methods=['POST'])
@admin_required
def add_mapping():
    mapping_type = request.form.get('mapping_type')
    supabase = get_supabase_client()

    if mapping_type == 'observer_child':
        observer_id = request.form.get('observer_id')
        child_id = request.form.get('child_id')

        mapping_data = {
            "id": str(uuid.uuid4()),
            "observer_id": observer_id,
            "child_id": child_id,
            "created_at": datetime.now().isoformat()
        }

        try:
            supabase.table('observer_child_mappings').insert(mapping_data).execute()
            flash('Observer-Child mapping added successfully!', 'success')
        except Exception as e:
            flash(f'Error adding mapping: {str(e)}', 'error')

    elif mapping_type == 'parent_child':
        parent_id = request.form.get('parent_id')
        child_id = request.form.get('child_id')

        try:
            supabase.table('users').update({'child_id': child_id}).eq('id', parent_id).execute()
            flash('Parent-Child mapping added successfully!', 'success')
        except Exception as e:
            flash(f'Error adding mapping: {str(e)}', 'error')

    return redirect(url_for('admin.mappings'))


@admin_bp.route('/bulk_upload_mappings', methods=['POST'])
@admin_required
def bulk_upload_mappings():
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('admin.mappings'))

    file = request.files['file']
    mapping_type = request.form.get('mapping_type')

    try:
        df = pd.read_csv(file)
        supabase = get_supabase_client()

        if mapping_type == 'observer_child':
            if not all(col in df.columns for col in ['observer_id', 'child_id']):
                flash('CSV must contain "observer_id" and "child_id" columns', 'error')
                return redirect(url_for('admin.mappings'))

            mappings_data = []
            for _, row in df.iterrows():
                mapping_data = {
                    "id": str(uuid.uuid4()),
                    "observer_id": row['observer_id'],
                    "child_id": row['child_id'],
                    "created_at": datetime.now().isoformat()
                }
                mappings_data.append(mapping_data)

            supabase.table('observer_child_mappings').insert(mappings_data).execute()
            flash(f'Successfully added {len(mappings_data)} observer-child mappings!', 'success')

        elif mapping_type == 'parent_child':
            if not all(col in df.columns for col in ['parent_email', 'child_name']):
                flash('CSV must contain "parent_email" and "child_name" columns', 'error')
                return redirect(url_for('admin.mappings'))

            children = get_children()
            parents = get_parents()
            child_name_to_id = {c['name'].lower(): c['id'] for c in children}
            parent_email_to_id = {p['email'].lower(): p['id'] for p in parents}

            success_count = 0
            for _, row in df.iterrows():
                parent_email = row['parent_email'].strip().lower()
                child_name = row['child_name'].strip().lower()

                parent_id = parent_email_to_id.get(parent_email)
                child_id = child_name_to_id.get(child_name)

                if parent_id and child_id:
                    supabase.table('users').update({'child_id': child_id}).eq('id', parent_id).execute()
                    success_count += 1

            flash(f'Successfully mapped {success_count} parent-child relationships!', 'success')

    except Exception as e:
        flash(f'Error processing file: {str(e)}', 'error')

    return redirect(url_for('admin.mappings'))


@admin_bp.route('/process_reports')
@admin_required
def process_reports():
    try:
        observers = get_observers()
        return render_template('admin/process_reports.html', observers=observers)
    except Exception as e:
        flash(f'Error loading process reports: {str(e)}', 'error')
        return render_template('admin/process_reports.html', observers=[])


@admin_bp.route('/get_observer_children/<observer_id>')
@admin_required
def get_observer_children_api(observer_id):
    try:
        children = get_observer_children(observer_id)
        return jsonify(children)
    except Exception as e:
        return jsonify([])


@admin_bp.route('/process_observation', methods=['POST'])
@admin_required
def process_observation():
    observer_id = request.form.get('observer_id')
    child_id = request.form.get('child_id')
    processing_mode = request.form.get('processing_mode')
    session_date = request.form.get('session_date')
    session_start = request.form.get('session_start')
    session_end = request.form.get('session_end')

    try:
        supabase = get_supabase_client()

        observer = supabase.table('users').select("name").eq('id', observer_id).execute().data
        child = supabase.table('children').select("name").eq('id', child_id).execute().data

        observer_name = observer[0]['name'] if observer else 'Unknown Observer'
        child_name = child[0]['name'] if child else 'Unknown Child'

        user_info = {
            'student_name': child_name,
            'observer_name': observer_name,
            'session_date': session_date,
            'session_start': session_start,
            'session_end': session_end,
            'child_id': child_id
        }

        extractor = ObservationExtractor()
        observation_id = str(uuid.uuid4())

        if processing_mode == 'ocr':
            if 'file' not in request.files:
                flash('No file uploaded', 'error')
                return redirect(url_for('admin.process_reports'))

            file = request.files['file']

            # Extract text and process
            extracted_text = extractor.extract_text_with_ocr(file)
            structured_data = extractor.process_with_groq(extracted_text)
            observations_text = structured_data.get("observations", "")

            # Upload file to storage
            file.seek(0)
            file_url = upload_file_to_storage(
                file.read(),
                file.filename,
                f"image/{file.content_type.split('/')[1]}"
            )

            # Generate formatted report
            report = extractor.generate_report_from_text(observations_text, user_info)

            # Save observation - store formatted report in full_data
            observation_data = {
                "id": observation_id,
                "student_id": child_id,
                "username": observer_id,
                "student_name": structured_data.get("studentName", child_name),
                "observer_name": observer_name,
                "class_name": structured_data.get("className", ""),
                "date": structured_data.get("date", session_date),
                "observations": observations_text,
                "strengths": json.dumps(structured_data.get("strengths", [])),
                "areas_of_development": json.dumps(structured_data.get("areasOfDevelopment", [])),
                "recommendations": json.dumps(structured_data.get("recommendations", [])),
                "timestamp": datetime.now().isoformat(),
                "filename": file.filename,
                "full_data": json.dumps({
                    **structured_data,
                    "formatted_report": report
                }),
                "theme_of_day": structured_data.get("themeOfDay", ""),
                "curiosity_seed": structured_data.get("curiositySeed", ""),
                "processed_by_admin": True,
                "file_url": file_url,
                # NEW: Initialize peer review fields
                "peer_reviews_required": 1,
                "peer_reviews_completed": 0,
                "peer_review_status": "pending"
            }

            # Save processed data separately
            processed_data = {
                "id": str(uuid.uuid4()),
                "child_id": child_id,
                "observer_id": observer_id,
                "processing_type": "ocr",
                "extracted_text": extracted_text,
                "structured_data": json.dumps(structured_data),
                "generated_report": report,
                "timestamp": datetime.now().isoformat(),
                "file_url": file_url
            }

            # Save to database
            supabase.table('observations').insert(observation_data).execute()
            supabase.table('processed_observations').insert(processed_data).execute()

            flash('OCR observation processed and saved successfully!', 'success')

        elif processing_mode == 'audio':
            if 'file' not in request.files:
                flash('No file uploaded', 'error')
                return redirect(url_for('admin.process_reports'))

            file = request.files['file']

            # Transcribe audio
            transcript = extractor.transcribe_with_assemblyai(file)

            # Upload file to storage
            file.seek(0)
            file_url = upload_file_to_storage(
                file.read(),
                file.filename,
                f"audio/{file.content_type.split('/')[1]}"
            )

            # Generate formatted report from transcript
            report = extractor.generate_report_from_text(transcript, user_info)

            # Save observation - store formatted report in full_data
            observation_data = {
                "id": observation_id,
                "student_id": child_id,
                "username": observer_id,
                "student_name": child_name,
                "observer_name": observer_name,
                "class_name": "",
                "date": session_date,
                "observations": transcript,
                "strengths": json.dumps([]),
                "areas_of_development": json.dumps([]),
                "recommendations": json.dumps([]),
                "timestamp": datetime.now().isoformat(),
                "filename": file.filename,
                "full_data": json.dumps({
                    "transcript": transcript,
                    "report": report,
                    "formatted_report": report
                }),
                "theme_of_day": "",
                "curiosity_seed": "",
                "processed_by_admin": True,
                "file_url": file_url,
                # NEW: Initialize peer review fields
                "peer_reviews_required": 1,
                "peer_reviews_completed": 0,
                "peer_review_status": "pending"
            }

            # Save processed data separately
            processed_data = {
                "id": str(uuid.uuid4()),
                "child_id": child_id,
                "observer_id": observer_id,
                "processing_type": "audio",
                "extracted_text": transcript,
                "structured_data": json.dumps({"transcript": transcript}),
                "generated_report": report,
                "timestamp": datetime.now().isoformat(),
                "file_url": file_url
            }

            # Save to database
            supabase.table('observations').insert(observation_data).execute()
            supabase.table('processed_observations').insert(processed_data).execute()

            flash('Audio observation processed and saved successfully!', 'success')

        # Store report ID in session for downloads
        session['last_admin_report_id'] = observation_id
        session['last_admin_report'] = report

    except Exception as e:
        flash(f'Error processing observation: {str(e)}', 'error')

    return redirect(url_for('admin.process_reports'))


@admin_bp.route('/download_admin_report')
@admin_required
def download_admin_report():
    try:
        report_id = session.get('last_admin_report_id')
        if not report_id:
            flash('No report available for download', 'error')
            return redirect(url_for('admin.process_reports'))

        # Get the report from database
        supabase = get_supabase_client()
        report_data = supabase.table('observations').select("*").eq('id', report_id).execute().data

        if not report_data:
            flash('Report not found', 'error')
            return redirect(url_for('admin.process_reports'))

        report = report_data[0]

        # Get formatted report from full_data
        formatted_report = None
        if report.get('full_data'):
            try:
                full_data = json.loads(report['full_data'])
                formatted_report = full_data.get('formatted_report')
            except:
                pass

        if not formatted_report:
            flash('No formatted report available', 'error')
            return redirect(url_for('admin.process_reports'))

        # Create Word document
        extractor = ObservationExtractor()
        doc_buffer = extractor.create_word_document_with_emojis(formatted_report)

        # Create filename
        student_name = report['student_name']
        if student_name:
            clean_name = re.sub(r'[^\w\s-]', '', student_name).strip()
            clean_name = re.sub(r'[-\s]+', '_', clean_name)
        else:
            clean_name = 'Student'

        date = report['date'] if report['date'] else datetime.now().strftime('%Y-%m-%d')
        filename = f"admin_report_{clean_name}_{date}.docx"

        return send_file(
            doc_buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )

    except Exception as e:
        flash(f'Error downloading report: {str(e)}', 'error')
        return redirect(url_for('admin.process_reports'))


@admin_bp.route('/download_admin_pdf')
@admin_required
def download_admin_pdf():
    try:
        report_id = session.get('last_admin_report_id')
        if not report_id:
            flash('No report available for download', 'error')
            return redirect(url_for('admin.process_reports'))

        # Get the report from database
        supabase = get_supabase_client()
        report_data = supabase.table('observations').select("*").eq('id', report_id).execute().data

        if not report_data:
            flash('Report not found', 'error')
            return redirect(url_for('admin.process_reports'))

        report = report_data[0]

        # Get formatted report
        formatted_report = None
        if report.get('full_data'):
            try:
                full_data = json.loads(report['full_data'])
                formatted_report = full_data.get('formatted_report')
            except:
                pass

        if not formatted_report:
            flash('No formatted report available', 'error')
            return redirect(url_for('admin.process_reports'))

        # Create PDF
        extractor = ObservationExtractor()
        pdf_buffer = extractor.create_pdf_with_emojis(formatted_report)

        # Create filename
        student_name = report['student_name']
        if student_name:
            clean_name = re.sub(r'[^\w\s-]', '', student_name).strip()
            clean_name = re.sub(r'[-\s]+', '_', clean_name)
        else:
            clean_name = 'Student'

        date = report['date'] if report['date'] else datetime.now().strftime('%Y-%m-%d')
        filename = f"admin_report_{clean_name}_{date}.pdf"

        return send_file(
            pdf_buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/pdf'
        )

    except Exception as e:
        flash(f'Error downloading PDF: {str(e)}', 'error')
        return redirect(url_for('admin.process_reports'))


@admin_bp.route('/email_report', methods=['POST'])
@admin_required
def email_report():
    try:
        report_id = session.get('last_admin_report_id')
        recipient_email = request.form.get('recipient_email')
        subject = request.form.get('subject', 'Observation Report (Admin Processed)')
        additional_message = request.form.get('additional_message', '')

        if not report_id or not recipient_email:
            return jsonify({'success': False, 'error': 'Missing report or email'})

        # Get the report from database
        supabase = get_supabase_client()
        report_data = supabase.table('observations').select("*").eq('id', report_id).execute().data

        if not report_data:
            return jsonify({'success': False, 'error': 'Report not found'})

        report = report_data[0]

        # Get formatted report
        formatted_report = None
        if report.get('full_data'):
            try:
                full_data = json.loads(report['full_data'])
                formatted_report = full_data.get('formatted_report')
            except:
                pass

        if not formatted_report:
            return jsonify({'success': False, 'error': 'No formatted report available'})

        # Prepare email content
        email_content = f"""
{additional_message}

{formatted_report}

---
This report was processed by the administrator.
"""

        # Send email
        extractor = ObservationExtractor()
        success, message = extractor.send_email(recipient_email, subject, email_content)

        return jsonify({'success': success, 'message': message})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@admin_bp.route('/delete_user/<user_id>')
@admin_required
def delete_user(user_id):
    try:
        supabase = get_supabase_client()
        supabase.table('users').delete().eq('id', user_id).execute()
        flash('User deleted successfully!', 'success')
    except Exception as e:
        flash(f'Error deleting user: {str(e)}', 'error')

    return redirect(url_for('admin.user_management'))


@admin_bp.route('/delete_mapping/<mapping_id>')
@admin_required
def delete_mapping(mapping_id):
    try:
        supabase = get_supabase_client()
        supabase.table('observer_child_mappings').delete().eq('id', mapping_id).execute()
        flash('Mapping deleted successfully!', 'success')
    except Exception as e:
        flash(f'Error deleting mapping: {str(e)}', 'error')

    return redirect(url_for('admin.mappings'))


# NEW MULTI-TENANT ROUTES

@admin_bp.route('/organizations')
@admin_required
def manage_organizations():
    """Manage all organizations"""
    try:
        organizations = get_organizations()
        return render_template('admin/organizations.html', organizations=organizations)
    except Exception as e:
        flash(f'Error loading organizations: {str(e)}', 'error')
        return render_template('admin/organizations.html', organizations=[])


@admin_bp.route('/organizations/create', methods=['GET', 'POST'])
@admin_required
def create_organization_route():
    """Create new organization"""
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        contact_email = request.form.get('contact_email')
        contact_phone = request.form.get('contact_phone')
        address = request.form.get('address')

        result = create_organization(name, description, contact_email, contact_phone, address)

        if result:
            flash('Organization created successfully!', 'success')
            return redirect(url_for('admin.manage_organizations'))
        else:
            flash('Error creating organization.', 'error')

    return render_template('admin/create_organization.html')


@admin_bp.route('/observer_applications')
@admin_required
def observer_applications():
    """View and manage observer applications"""
    try:
        supabase = get_supabase_client()
        
        # Get all observer applications with counts by status
        applications_response = supabase.table('observer_applications').select('*').order('applied_at', desc=True).execute()
        applications = applications_response.data if applications_response.data else []
        
        # Get organizations for assignment
        organizations_response = supabase.table('organizations').select('*').order('name').execute()
        organizations = organizations_response.data if organizations_response.data else []
        
        # FIXED: Calculate statistics using application_status column
        pending_count = len([app for app in applications if app.get('application_status') == 'pending'])
        approved_count = len([app for app in applications if app.get('application_status') == 'approved'])
        rejected_count = len([app for app in applications if app.get('application_status') == 'rejected'])
        
        stats = {
            'total': len(applications),
            'pending': pending_count,
            'approved': approved_count,
            'rejected': rejected_count
        }
        
        return render_template('admin/observer_applications.html', 
                             applications=applications,
                             organizations=organizations,
                             stats=stats)
    except Exception as e:
        logger.error(f"Error loading observer applications: {e}")
        flash(f'Error loading applications: {str(e)}', 'error')
        return render_template('admin/observer_applications.html', 
                             applications=[], 
                             organizations=[], 
                             stats={'total': 0, 'pending': 0, 'approved': 0, 'rejected': 0})


# Keep only this one - the complete version
@admin_bp.route('/review_observer_application/<application_id>', methods=['POST'])
@admin_required
def review_observer_application(application_id):
    """Approve or reject observer application"""
    try:
        admin_id = session.get('user_id')
        action = request.form.get('action')
        organization_id = request.form.get('organization_id')
        rejection_reason = request.form.get('rejection_reason', '')
        admin_comments = request.form.get('admin_comments', '')

        supabase = get_supabase_client()

        # Get application details
        application = supabase.table('observer_applications').select('*').eq('id', application_id).execute()
        if not application.data:
            flash('Application not found', 'error')
            return redirect(url_for('admin.observer_applications'))

        app_data = application.data[0]

        if action == 'approve':
            if not organization_id:
                flash('Please select an organization for the observer', 'error')
                return redirect(url_for('admin.view_observer_application', application_id=application_id))

            # Generate temporary password
            temp_password = f"Observer{uuid.uuid4().hex[:8]}"

            # Handle admin_id properly
            if admin_id == 'admin' or not admin_id:
                admin_uuid = str(uuid.uuid4())
            else:
                admin_uuid = admin_id

            # Create observer user account
            observer_data = {
                'id': str(uuid.uuid4()),
                'email': app_data['applicant_email'],
                'name': app_data['applicant_name'],
                'password': temp_password,
                'role': 'Observer',
                'organization_id': organization_id,
                'phone': app_data.get('applicant_phone'),
                'created_at': datetime.now().isoformat(),
                'created_by': admin_uuid
            }

            # Insert observer user
            user_result = supabase.table('users').insert(observer_data).execute()

            if user_result.data:
                # FIXED: Update using correct column names
                update_data = {
                    'application_status': 'approved',  # Use application_status instead of status
                    'status': 'approved',  # Also update status for consistency
                    'reviewed_at': datetime.now().isoformat(),
                    'reviewed_by': admin_uuid,
                    'organization_id': organization_id,
                    'temp_password': temp_password,
                    'admin_comments': admin_comments,
                    'review_notes': admin_comments  # Also update review_notes
                }

                supabase.table('observer_applications').update(update_data).eq('id', application_id).execute()

                # Get organization name
                try:
                    org_response = supabase.table('organizations').select('name').eq('id', organization_id).execute()
                    org_name = org_response.data[0]['name'] if org_response.data else 'Unknown Organization'
                except:
                    org_name = 'Unknown Organization'

                flash(
                    f'Observer application approved! {app_data["applicant_name"]} has been created as an observer for {org_name}.',
                    'success')
                return redirect(url_for('admin.observer_applications'))
            else:
                flash('Error creating observer account', 'error')
                return redirect(url_for('admin.view_observer_application', application_id=application_id))

        elif action == 'reject':
            if not rejection_reason:
                flash('Please provide a reason for rejection', 'error')
                return redirect(url_for('admin.view_observer_application', application_id=application_id))

            # Handle admin_id for rejection
            if admin_id == 'admin' or not admin_id:
                admin_uuid = str(uuid.uuid4())
            else:
                admin_uuid = admin_id

            # FIXED: Update using correct column names
            update_data = {
                'application_status': 'rejected',  # Use application_status instead of status
                'status': 'rejected',  # Also update status for consistency
                'reviewed_at': datetime.now().isoformat(),
                'reviewed_by': admin_uuid,
                'rejection_reason': rejection_reason,
                'admin_comments': admin_comments,
                'review_notes': f"Rejected: {rejection_reason}"  # Also update review_notes
            }

            supabase.table('observer_applications').update(update_data).eq('id', application_id).execute()

            flash('Observer application rejected successfully.', 'success')
            return redirect(url_for('admin.observer_applications'))

    except Exception as e:
        logger.error(f"Error reviewing observer application: {e}")
        flash(f'Error reviewing application: {str(e)}', 'error')
        return redirect(url_for('admin.view_observer_application', application_id=application_id))


@admin_bp.route('/view_observer_application/<application_id>')
@admin_required
def view_observer_application(application_id):
    """View detailed observer application"""
    try:
        supabase = get_supabase_client()

        # Get application details
        application = supabase.table('observer_applications').select('*').eq('id', application_id).execute()
        if not application.data:
            flash('Application not found', 'error')
            return redirect(url_for('admin.observer_applications'))

        app_data = application.data[0]

        # Get reviewer info if reviewed
        reviewer_info = None
        if app_data.get('reviewed_by'):
            reviewer = supabase.table('users').select('name').eq('id', app_data['reviewed_by']).execute()
            reviewer_info = reviewer.data[0] if reviewer.data else None

        # Get organization info if assigned
        organization_info = None
        if app_data.get('organization_id'):
            org = supabase.table('organizations').select('name').eq('id', app_data['organization_id']).execute()
            organization_info = org.data[0] if org.data else None

        # Get organizations for assignment dropdown
        organizations_response = supabase.table('organizations').select('*').order('name').execute()
        organizations = organizations_response.data if organizations_response.data else []

        return render_template('admin/view_observer_application.html',
                               application=app_data,
                               reviewer_info=reviewer_info,
                               organization_info=organization_info,
                               organizations=organizations)  # ADD THIS LINE
    except Exception as e:
        logger.error(f"Error viewing observer application: {e}")
        flash(f'Error loading application: {str(e)}', 'error')
        return redirect(url_for('admin.observer_applications'))


def send_observer_decision_notification(app_data, organization_id, org_name, approved, temp_password=None, admin_comments=None, rejection_reason=None):
    """Send email notification about observer application decision"""
    try:
        if approved:
            message = f"""
            Congratulations! Your observer application has been approved.
            
            Organization: {org_name}
            Temporary Password: {temp_password}
            
            Please log in and change your password immediately.
            
            Admin Comments: {admin_comments or 'None'}
            """
            logger.info(f"Observer application approved for {app_data['applicant_email']} - assigned to {org_name}")
        else:
            message = f"""
            We regret to inform you that your observer application has been rejected.
            
            Reason: {rejection_reason}
            Admin Comments: {admin_comments or 'None'}
            
            You may reapply in the future if circumstances change.
            """
            logger.info(f"Observer application rejected for {app_data['applicant_email']} - reason: {rejection_reason}")
            
        # TODO: Implement actual email sending here
        # send_email(app_data['applicant_email'], subject, message)
        
    except Exception as e:
        logger.error(f"Error sending observer decision notification: {e}")


# NEW: Observer-Organization Assignment Route
@admin_bp.route('/assign_observer_organization', methods=['POST'])
@admin_required
def assign_observer_organization():
    """Assign observer to organization"""
    observer_id = request.form.get('observer_id')
    organization_id = request.form.get('organization_id')

    try:
        supabase = get_supabase_client()
        supabase.table('users').update({
            'organization_id': organization_id
        }).eq('id', observer_id).execute()

        flash('Observer assigned to organization successfully!', 'success')
    except Exception as e:
        flash(f'Error assigning observer: {str(e)}', 'error')

    return redirect(url_for('admin.user_management'))


@admin_bp.route('/global_analytics')
@admin_required
def global_analytics():
    """Global analytics across all organizations"""
    try:
        organizations = get_organizations()
        supabase = get_supabase_client()

        analytics_data = []
        for org in organizations:
            try:
                org_data = {
                    'organization': org,
                    'observers': len(get_users_by_organization(org['id'], 'Observer')),
                    'parents': len(get_users_by_organization(org['id'], 'Parent')),
                    'principals': len(get_users_by_organization(org['id'], 'Principal')),
                    'children': len(
                        supabase.table('children').select('id').eq('organization_id', org['id']).execute().data),
                    'observations': 0  # Simplified for now due to complex join
                }
                analytics_data.append(org_data)
            except Exception as e:
                logger.warning(f"Error getting analytics for organization {org.get('name', 'Unknown')}: {e}")

        return render_template('admin/global_analytics.html', analytics_data=analytics_data)
    except Exception as e:
        flash(f'Error loading global analytics: {str(e)}', 'error')
        return render_template('admin/global_analytics.html', analytics_data=[])


@admin_bp.route('/analytics')
@admin_required
def analytics():
    """Detailed analytics page"""
    try:
        supabase = get_supabase_client()

        # Get comprehensive analytics data
        users_response = supabase.table('users').select("id", count="exact").execute()
        observers_response = supabase.table('users').select("id", count="exact").eq('role', 'Observer').execute()
        parents_response = supabase.table('users').select("id", count="exact").eq('role', 'Parent').execute()
        principals_response = supabase.table('users').select("id", count="exact").eq('role', 'Principal').execute()
        children_response = supabase.table('children').select("id", count="exact").execute()
        observations_response = supabase.table('observations').select("id", count="exact").execute()

        try:
            organizations_response = supabase.table('organizations').select("id", count="exact").execute()
        except Exception as e:
            logger.warning(f"Organizations table might not exist: {e}")
            organizations_response = type('obj', (object,), {'count': 0})()

        # Get recent activity
        recent_observations = supabase.table('observations').select("""
            id, student_name, observer_name, date, timestamp
        """).order('timestamp', desc=True).limit(20).execute().data

        # Get system status
        total_storage_files = supabase.table('observations').select("file_url").not_.is_('file_url', 'null').execute()

        analytics = {
            'total_users': users_response.count if users_response.count else 0,
            'observers_count': observers_response.count if observers_response.count else 0,
            'parents_count': parents_response.count if parents_response.count else 0,
            'principals_count': principals_response.count if principals_response.count else 0,
            'children_count': children_response.count if children_response.count else 0,
            'observations_count': observations_response.count if observations_response.count else 0,
            'organizations_count': organizations_response.count if organizations_response.count else 0,
            'storage_files': len(total_storage_files.data) if total_storage_files.data else 0,
            'recent_observations': recent_observations
        }

        return render_template('admin/analytics.html', analytics=analytics)
    except Exception as e:
        flash(f'Error loading analytics: {str(e)}', 'error')
        return render_template('admin/analytics.html', analytics={})


@admin_bp.route('/logs')
@admin_required
def logs():
    """System logs page"""
    try:
        supabase = get_supabase_client()

        # Get system logs (you may need to create a logs table)
        logs = []  # Placeholder - implement based on your logging system

        return render_template('admin/logs.html', logs=logs)
    except Exception as e:
        flash(f'Error loading logs: {str(e)}', 'error')
        return render_template('admin/logs.html', logs=[])


@admin_bp.route('/fix_organization_assignments')
@admin_required
def fix_organization_assignments():
    """Fix organization assignments for existing data"""
    try:
        supabase = get_supabase_client()

        # Get all organizations
        orgs = supabase.table('organizations').select('*').execute().data

        if not orgs:
            flash('No organizations found. Please create an organization first.', 'error')
            return redirect(url_for('admin.dashboard'))

        # Use the first organization as default for unassigned users
        default_org_id = orgs[0]['id']

        # Update users without organization_id
        unassigned_users = supabase.table('users').select('*').is_('organization_id', 'null').execute().data

        for user in unassigned_users:
            supabase.table('users').update({
                'organization_id': default_org_id
            }).eq('id', user['id']).execute()

        # Update children without organization_id
        unassigned_children = supabase.table('children').select('*').is_('organization_id', 'null').execute().data

        for child in unassigned_children:
            supabase.table('children').update({
                'organization_id': default_org_id
            }).eq('id', child['id']).execute()

        flash(f'Fixed organization assignments: {len(unassigned_users)} users and {len(unassigned_children)} children assigned to {orgs[0]["name"]}', 'success')

    except Exception as e:
        flash(f'Error fixing assignments: {str(e)}', 'error')

    return redirect(url_for('admin.dashboard'))


@admin_bp.route('/assign_child_organization', methods=['POST'])
@admin_required
def assign_child_organization():
    """Assign child to organization and auto-assign associated parent"""
    try:
        child_id = request.form.get('child_id')
        organization_id = request.form.get('organization_id')

        logger.info(f"Assigning child to org: child_id={child_id}, organization_id={organization_id}")

        if not child_id or not organization_id:
            flash('Please select both child and organization.', 'error')
            return redirect(url_for('admin.user_management'))

        supabase = get_supabase_client()

        # Verify child exists
        child_check = supabase.table('children').select('*').eq('id', child_id).execute()
        if not child_check.data:
            flash('Child not found.', 'error')
            return redirect(url_for('admin.user_management'))

        # Verify organization exists
        org_check = supabase.table('organizations').select('*').eq('id', organization_id).execute()
        if not org_check.data:
            flash('Organization not found.', 'error')
            return redirect(url_for('admin.user_management'))

        # Update child's organization
        result = supabase.table('children').update({
            'organization_id': organization_id
        }).eq('id', child_id).execute()

        if result.data:
            logger.info(f"Successfully assigned child {child_id} to organization {organization_id}")

            # Auto-assign associated parent to same organization
            try:
                auto_assign_parent_to_organization(child_id, organization_id)
                logger.info(f"Auto-assigned parent for child {child_id}")

                # Log the assignment for audit trail (with proper admin_id)
                admin_id = session.get('user_id')
                log_organization_change(child_id, 'child', organization_id, 'assigned', admin_id)

                flash('Child assigned to organization successfully! Associated parent also assigned.', 'success')
            except Exception as parent_error:
                logger.warning(f'Child assigned but parent auto-assignment failed: {parent_error}')
                flash('Child assigned to organization successfully! (Parent auto-assignment had issues)', 'warning')
        else:
            logger.error(f"Failed to assign child {child_id} to organization {organization_id} - no data returned")
            flash('Error assigning child to organization. Please try again.', 'error')

    except Exception as e:
        logger.error(f'Error in assign_child_organization: {str(e)}')
        flash(f'Error assigning child: {str(e)}', 'error')

    return redirect(url_for('admin.user_management'))


@admin_bp.route('/bulk_assign_children_organization', methods=['POST'])
@admin_required
def bulk_assign_children_organization():
    """Bulk assign children to organizations"""
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('admin.user_management'))

    file = request.files['file']

    try:
        df = pd.read_csv(file)
        supabase = get_supabase_client()

        if not all(col in df.columns for col in ['child_name', 'organization_name']):
            flash('CSV must contain "child_name" and "organization_name" columns', 'error')
            return redirect(url_for('admin.user_management'))

        # Get all children and organizations for mapping
        children = get_children()
        organizations = get_organizations()

        child_name_to_id = {child['name'].lower(): child['id'] for child in children}
        org_name_to_id = {org['name'].lower(): org['id'] for org in organizations}

        success_count = 0
        for _, row in df.iterrows():
            child_name = row['child_name'].strip().lower()
            org_name = row['organization_name'].strip().lower()

            child_id = child_name_to_id.get(child_name)
            org_id = org_name_to_id.get(org_name)

            if child_id and org_id:
                # Update child's organization
                result = supabase.table('children').update({
                    'organization_id': org_id
                }).eq('id', child_id).execute()

                if result.data:
                    # Auto-assign parent
                    auto_assign_parent_to_organization(child_id, org_id)
                    # Log the assignment
                    log_organization_change(child_id, 'child', org_id, 'bulk_assigned')
                    success_count += 1

        flash(f'Successfully assigned {success_count} children to organizations!', 'success')

    except Exception as e:
        logger.error(f'Error processing bulk assignment: {str(e)}')
        flash(f'Error processing file: {str(e)}', 'error')

    return redirect(url_for('admin.user_management'))


@admin_bp.route('/organization_audit_log')
@admin_required
def organization_audit_log():
    """View organization assignment audit log"""
    try:
        supabase = get_supabase_client()

        # Get audit log entries
        audit_logs = supabase.table('organization_audit_log').select('*').order('created_at', desc=True).limit(100).execute()

        return render_template('admin/organization_audit_log.html',
                             audit_logs=audit_logs.data if audit_logs.data else [])
    except Exception as e:
        logger.error(f'Error loading audit log: {str(e)}')
        flash(f'Error loading audit log: {str(e)}', 'error')
        return render_template('admin/organization_audit_log.html', audit_logs=[])


def log_organization_change(entity_id, entity_type, organization_id, action, admin_id=None):
    """Log organization assignment changes for audit trail - FIXED"""
    try:
        supabase = get_supabase_client()

        # Get admin_id from session if not provided
        if not admin_id:
            admin_id = session.get('user_id')

        log_data = {
            'id': str(uuid.uuid4()),
            'entity_id': entity_id,
            'entity_type': entity_type,
            'organization_id': organization_id,
            'action': action,
            'admin_id': admin_id,  # This should be a proper UUID
            'created_at': datetime.now().isoformat()
        }

        # Only log if we have a valid admin_id
        if admin_id and admin_id != 'admin':  # Avoid the string 'admin' that was causing UUID errors
            supabase.table('organization_audit_log').insert(log_data).execute()
            logger.info(f"Logged organization change: {entity_type} {entity_id} {action} to org {organization_id}")
        else:
            logger.warning(f"Skipping audit log - invalid admin_id: {admin_id}")

    except Exception as e:
        logger.error(f'Error logging organization change: {e}')


def log_mapping_change(entity1_id, entity2_id, mapping_type, action):
    """Log mapping changes for audit trail"""
    try:
        supabase = get_supabase_client()

        log_data = {
            'id': str(uuid.uuid4()),
            'entity1_id': entity1_id,
            'entity2_id': entity2_id,
            'mapping_type': mapping_type,
            'action': action,
            'admin_id': session.get('user_id'),
            'created_at': datetime.now().isoformat()
        }

        supabase.table('mapping_audit_log').insert(log_data).execute()
    except Exception as e:
        logger.error(f'Error logging mapping change: {e}')


@admin_bp.route('/assign_user_organization', methods=['POST'])
@admin_required
def assign_user_organization():
    """Assign user to organization"""
    try:
        user_id = request.form.get('user_id')
        organization_id = request.form.get('organization_id')

        logger.info(f"Assigning user to org: user_id={user_id}, organization_id={organization_id}")

        if not user_id or not organization_id:
            flash('Please select both user and organization.', 'error')
            return redirect(url_for('admin.user_management'))

        supabase = get_supabase_client()

        # Verify user exists
        user_check = supabase.table('users').select('*').eq('id', user_id).execute()
        if not user_check.data:
            flash('User not found.', 'error')
            return redirect(url_for('admin.user_management'))

        # Verify organization exists
        org_check = supabase.table('organizations').select('*').eq('id', organization_id).execute()
        if not org_check.data:
            flash('Organization not found.', 'error')
            return redirect(url_for('admin.user_management'))

        # Update user's organization
        result = supabase.table('users').update({
            'organization_id': organization_id
        }).eq('id', user_id).execute()

        if result.data:
            logger.info(f"Successfully assigned user {user_id} to organization {organization_id}")

            # Log the assignment for audit trail (with proper admin_id)
            admin_id = session.get('user_id')
            log_organization_change(user_id, 'user', organization_id, 'assigned', admin_id)

            flash('User assigned to organization successfully!', 'success')
        else:
            logger.error(f"Failed to assign user {user_id} to organization {organization_id} - no data returned")
            flash('Error assigning user to organization. Please try again.', 'error')

    except Exception as e:
        logger.error(f'Error in assign_user_organization: {str(e)}')
        flash(f'Error assigning user: {str(e)}', 'error')

    return redirect(url_for('admin.user_management'))


@admin_bp.route('/create_observer_child_mapping', methods=['POST'])
@admin_required
def create_observer_child_mapping():
    """Create observer-child mapping"""
    try:
        observer_id = request.form.get('observer_id')
        child_id = request.form.get('child_id')

        if not observer_id or not child_id:
            flash('Please select both observer and child.', 'error')
            return redirect(url_for('admin.user_management'))

        supabase = get_supabase_client()

        # Check if mapping already exists
        existing = supabase.table('observer_child_mappings').select('id').eq('observer_id', observer_id).eq('child_id', child_id).execute()

        if existing.data:
            flash('This observer-child mapping already exists.', 'warning')
            return redirect(url_for('admin.user_management'))

        # Create mapping
        mapping_data = {
            "id": str(uuid.uuid4()),
            "observer_id": observer_id,
            "child_id": child_id,
            "created_at": datetime.now().isoformat()
        }

        result = supabase.table('observer_child_mappings').insert(mapping_data).execute()

        if result.data:
            # Log the mapping creation
            log_mapping_change(observer_id, child_id, 'observer_child', 'created')
            flash('Observer-Child mapping created successfully!', 'success')
        else:
            flash('Error creating mapping. Please try again.', 'error')

    except Exception as e:
        logger.error(f'Error creating observer-child mapping: {str(e)}')
        flash(f'Error creating mapping: {str(e)}', 'error')

    return redirect(url_for('admin.user_management'))


@admin_bp.route('/create_parent_child_mapping', methods=['POST'])
@admin_required
def create_parent_child_mapping():
    """Create parent-child mapping"""
    try:
        parent_id = request.form.get('parent_id')
        child_id = request.form.get('child_id')

        if not parent_id or not child_id:
            flash('Please select both parent and child.', 'error')
            return redirect(url_for('admin.user_management'))

        supabase = get_supabase_client()

        # Update parent's child_id
        result = supabase.table('users').update({
            'child_id': child_id
        }).eq('id', parent_id).execute()

        if result.data:
            # Log the mapping creation
            log_mapping_change(parent_id, child_id, 'parent_child', 'created')
            flash('Parent-Child mapping created successfully!', 'success')
        else:
            flash('Error creating mapping. Please try again.', 'error')

    except Exception as e:
        logger.error(f'Error creating parent-child mapping: {str(e)}')
        flash(f'Error creating mapping: {str(e)}', 'error')

    return redirect(url_for('admin.user_management'))


@admin_bp.route('/download_csv_template/<template_type>')
@admin_required
def download_csv_template(template_type):
    """Download CSV templates for bulk upload"""
    try:
        if template_type == 'children':
            csv_content = "name,birth_date,grade\nJohn Doe,2015-01-01,Grade 1\nJane Smith,2016-02-15,Grade 2"
            filename = "children_template.csv"
        elif template_type == 'parents':
            csv_content = "name,email,password\nJohn Parent,john.parent@example.com,password123\nJane Parent,jane.parent@example.com,password456"
            filename = "parents_template.csv"
        elif template_type == 'observers':
            csv_content = "name,email,password\nJohn Observer,john.observer@example.com,password123\nJane Observer,jane.observer@example.com,password456"
            filename = "observers_template.csv"
        elif template_type == 'principals':
            csv_content = "name,email,password\nJohn Principal,john.principal@example.com,password123\nJane Principal,jane.principal@example.com,password456"
            filename = "principals_template.csv"
        else:
            flash('Invalid template type', 'error')
            return redirect(url_for('admin.user_management'))

        output = io.StringIO()
        output.write(csv_content)
        output.seek(0)

        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={"Content-Disposition": f"attachment;filename={filename}"}
        )

    except Exception as e:
        flash(f'Error generating template: {str(e)}', 'error')
        return redirect(url_for('admin.user_management'))


@admin_bp.route('/principal_applications')
@admin_required
def principal_applications():
    """View and manage principal applications"""
    try:
        supabase = get_supabase_client()
        # Get all principal applications with counts by status
        applications_response = supabase.table('principal_applications').select('*').order('applied_at', desc=True).execute()
        applications = applications_response.data if applications_response.data else []
        # Get organizations for assignment
        organizations_response = supabase.table('organizations').select('*').order('name').execute()
        organizations = organizations_response.data if organizations_response.data else []
        # Calculate statistics
        pending_count = len([app for app in applications if app.get('status') == 'pending'])
        approved_count = len([app for app in applications if app.get('status') == 'approved'])
        rejected_count = len([app for app in applications if app.get('status') == 'rejected'])
        stats = {
            'total': len(applications),
            'pending': pending_count,
            'approved': approved_count,
            'rejected': rejected_count
        }
        return render_template('admin/principal_applications.html',
                             applications=applications,
                             organizations=organizations,
                             stats=stats)
    except Exception as e:
        logger.error(f"Error loading principal applications: {e}")
        flash(f'Error loading applications: {str(e)}', 'error')
        return render_template('admin/principal_applications.html',
                             applications=[],
                             organizations=[],
                             stats={'total': 0, 'pending': 0, 'approved': 0, 'rejected': 0})


@admin_bp.route('/review_principal_application/<application_id>', methods=['POST'])
@admin_required
def review_principal_application(application_id):
    """Approve or reject principal application"""
    try:
        admin_id = session.get('user_id')
        action = request.form.get('action')
        organization_id = request.form.get('organization_id')
        rejection_reason = request.form.get('rejection_reason', '')
        admin_comments = request.form.get('admin_comments', '')
        
        # CRITICAL FIX: Validate all UUIDs before database operations
        try:
            uuid.UUID(application_id)  # Validate application_id
            if admin_id != 'admin':  # Check if admin_id is not the string "admin"
                uuid.UUID(admin_id)  # Validate admin_id if it's not "admin"
        except (ValueError, TypeError):
            flash('Invalid ID format detected', 'error')
            return redirect(url_for('admin.principal_applications'))
        
        supabase = get_supabase_client()
        
        # Get application details
        application = supabase.table('principal_applications').select('*').eq('id', application_id).execute()
        if not application.data:
            flash('Application not found', 'error')
            return redirect(url_for('admin.principal_applications'))
        
        app_data = application.data[0]
        
        if action == 'approve':
            if not organization_id:
                flash('Please select an organization for the principal', 'error')
                return redirect(url_for('admin.principal_applications'))
            
            # CRITICAL FIX: Validate organization_id
            try:
                uuid.UUID(organization_id)
            except (ValueError, TypeError):
                flash('Invalid organization ID format', 'error')
                return redirect(url_for('admin.principal_applications'))
            
            temp_password = f"Principal{uuid.uuid4().hex[:8]}"
            
            # CRITICAL FIX: Handle admin_id properly
            if admin_id == 'admin':
                # If admin_id is "admin", get the actual admin user ID from database
                admin_user = supabase.table('users').select('id').eq('role', 'Admin').limit(1).execute()
                if admin_user.data:
                    admin_uuid = admin_user.data[0]['id']
                else:
                    admin_uuid = str(uuid.uuid4())  # Create new UUID if no admin found
            else:
                admin_uuid = admin_id
            
            principal_data = {
                'id': str(uuid.uuid4()),
                'email': app_data['email'],
                'name': app_data['applicant_name'],
                'password': temp_password,
                'role': 'Principal',
                'organization_id': organization_id,
                'phone': app_data.get('phone'),
                'created_at': datetime.now().isoformat(),
                'created_by': admin_uuid,  # Use the validated UUID
                'temp_password': True
            }
            
            user_result = supabase.table('users').insert(principal_data).execute()
            
            if user_result.data:
                supabase.table('principal_applications').update({
                    'status': 'approved',
                    'reviewed_at': datetime.now().isoformat(),
                    'reviewed_by': admin_uuid,  # Use the validated UUID
                    'organization_id': organization_id,
                    'admin_comments': admin_comments,
                    'temp_password': temp_password
                }).eq('id', application_id).execute()
                
                org_name = next((org['name'] for org in get_organizations() if org['id'] == organization_id), 'Unknown')
                flash(f'Principal application approved! {app_data["applicant_name"]} has been created as a principal for {org_name}.', 'success')
            else:
                flash('Error creating principal account', 'error')
        
        elif action == 'reject':
            if not rejection_reason:
                flash('Please provide a reason for rejection', 'error')
                return redirect(url_for('admin.principal_applications'))
            
            # CRITICAL FIX: Handle admin_id for rejection too
            if admin_id == 'admin':
                admin_user = supabase.table('users').select('id').eq('role', 'Admin').limit(1).execute()
                if admin_user.data:
                    admin_uuid = admin_user.data[0]['id']
                else:
                    admin_uuid = str(uuid.uuid4())
            else:
                admin_uuid = admin_id
            
            supabase.table('principal_applications').update({
                'status': 'rejected',
                'reviewed_at': datetime.now().isoformat(),
                'reviewed_by': admin_uuid,  # Use the validated UUID
                'rejection_reason': rejection_reason,
                'admin_comments': admin_comments
            }).eq('id', application_id).execute()
            
            flash('Principal application rejected.', 'success')
        
    except Exception as e:
        logger.error(f"Error reviewing principal application: {e}")
        flash(f'Error reviewing application: {str(e)}', 'error')
    
    return redirect(url_for('admin.principal_applications'))


def send_principal_decision_notification(app_data, organization_id, org_name, approved, temp_password=None, admin_comments=None, rejection_reason=None):
    try:
        if approved:
            message = f"""
            Congratulations! Your principal application has been approved.
            Organization: {org_name}
            Temporary Password: {temp_password}
            Please log in and change your password immediately.
            Admin Comments: {admin_comments or 'None'}
            """
            logger.info(f"Principal application approved for {app_data['email']} - assigned to {org_name}")
        else:
            message = f"""
            We regret to inform you that your principal application has been rejected.
            Reason: {rejection_reason}
            Admin Comments: {admin_comments or 'None'}
            You may reapply in the future if circumstances change.
            """
            logger.info(f"Principal application rejected for {app_data['email']} - reason: {rejection_reason}")
        # TODO: Implement actual email sending here
    except Exception as e:
        logger.error(f"Error sending principal decision notification: {e}")


def create_admin_audit_log(admin_id, action, description):
    try:
        supabase = get_supabase_client()
        audit_data = {
            'id': str(uuid.uuid4()),
            'admin_id': admin_id,
            'action': action,
            'description': description,
            'timestamp': datetime.now().isoformat()
        }
        supabase.table('admin_audit_log').insert(audit_data).execute()
    except Exception as e:
        logger.error(f"Error creating audit log: {e}")
@admin_bp.route('/observer_report_counts')
@admin_required
def observer_report_counts():
    try:
        supabase = get_supabase_client()
        # Get all observers
        observers = supabase.table('users').select('id, name, email').eq('role', 'Observer').execute().data
        # Count processed reports for each observer
        data = []
        for obs in observers:
            processed_reports = supabase.table('observations').select('id').eq('username', obs['id']).eq('processed_by_admin', False).execute().data
            admin_processed = supabase.table('observations').select('id').eq('username', obs['id']).eq('processed_by_admin', True).execute().data
            data.append({
                'observer': obs,
                'count_observer': len(processed_reports),
                'count_admin': len(admin_processed),
                'total': len(processed_reports) + len(admin_processed)
            })
        return render_template('admin/observer_report_counts.html', data=data)
    except Exception as e:
        return render_template('admin/observer_report_counts.html', data=[], error=str(e))


@admin_bp.route('/view_principal_application/<application_id>')
@admin_required
def view_principal_application(application_id):
    """View detailed principal application"""
    try:
        supabase = get_supabase_client()

        # Get application details
        application = supabase.table('principal_applications').select('*').eq('id', application_id).execute()
        if not application.data:
            flash('Application not found', 'error')
            return redirect(url_for('admin.principal_applications'))

        app_data = application.data[0]

        # Get reviewer info if reviewed
        reviewer_info = None
        if app_data.get('reviewed_by'):
            reviewer = supabase.table('users').select('name').eq('id', app_data['reviewed_by']).execute()
            reviewer_info = reviewer.data[0] if reviewer.data else None

        # Get organization info if assigned
        organization_info = None
        if app_data.get('organization_id'):
            org = supabase.table('organizations').select('id', 'name').eq('id', app_data['organization_id']).execute()
            organization_info = org.data[0] if org.data else None

        # Get organizations for assignment dropdown
        organizations_response = supabase.table('organizations').select('*').order('name').execute()
        organizations = organizations_response.data if organizations_response.data else []

        return render_template(
            'admin/view_principal_application.html',
            application=app_data,
            reviewer_info=reviewer_info,
            organization_info=organization_info,
            organizations=organizations
        )
    except Exception as e:
        logger.error(f"Error viewing principal application: {e}")
        flash(f'Error loading application: {str(e)}', 'error')
        return redirect(url_for('admin.principal_applications'))
    