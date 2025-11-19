# routes/transcripts.py

from flask import Blueprint, request, jsonify, session
from flask_login import login_required
import logging

# 1. Import your ObservationExtractor class
#    You must adjust this import path to match your project structure.
#    I'm guessing its location, it might be in 'models' or 'services'
try:
    from models.observation_extractor import ObservationExtractor
except ImportError:
    # If the above fails, you might need to adjust the path
    # This is a common way to import from a parallel 'models' folder
    import sys
    import os
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from models.observation_extractor import ObservationExtractor


# Get a logger
logger = logging.getLogger(__name__)

# 2. Create a new Blueprint
transcripts_bp = Blueprint('transcripts', __name__)


# 3. Add the route to process and save a new transcript
@transcripts_bp.route('/process_and_save', methods=['POST'])
@login_required
def process_and_save_observation():
    """
    This route takes raw text and generates all necessary reports.
    """
    
    # 1. Get data from the frontend JSON request
    data = request.json
    raw_text = data.get('raw_text')
    child_id = data.get('child_id')
    
    # Get the user_info dict (student_name, observer_name, session_date, etc.)
    user_info = data.get('user_info', {}) 
    if 'child_id' not in user_info and child_id:
        user_info['child_id'] = child_id
    
    # 2. Get the logged-in observer's ID from the session
    observer_id = session.get('user_id') 
    
    if not all([raw_text, child_id, observer_id]):
        return jsonify({"error": "Missing required data: raw_text, child_id, or user session."}), 400
        
    try:
        # 3. Initialize your extractor
        extractor = ObservationExtractor()
        
        # 4. GENERATE the reports
        
        # --- Generate Conversational Transcript ---
        conversational_transcript = extractor.generate_conversational_transcript(raw_text)
        
        # --- Generate Daily Insights Report ---
        daily_report = extractor.generate_report_from_text(raw_text, user_info)
        
        
        
        # 6. Return the main report to the frontend to display
        return jsonify({
            "success": True,
            "daily_report": daily_report, # Send back the main report
            "message": "Reports generated successfully."
        })
    
    except Exception as e:
        logger.error(f"Error processing observation: {str(e)}")
        return jsonify({"error": f"An internal error occurred: {str(e)}"}), 500


# 4. Add a route to GET all transcripts for a child
@transcripts_bp.route('/get_transcripts/<child_id>', methods=['GET'])
@login_required
def get_transcripts_for_child(child_id):
    """
    An example route to fetch all saved transcripts for a specific child.
    """
    try:
        from models.database import get_supabase_client
        supabase = get_supabase_client()
        
        # Get the logged-in user's ID
        # user_id = session.get('user_id')
        
        # Fetch transcripts related to this child
        # TODO: Add RLS or a .eq('observer_id', user_id) check
        #       to ensure the user has permission to see them
        response = supabase.table('transcripts').select('*') \
            .eq('child_id', child_id) \
            .order('session_date', desc=True) \
            .execute()
            
        return jsonify(response.data)
            
    except Exception as e:
        logger.error(f"Error fetching transcripts: {str(e)}")
        return jsonify({"error": f"An internal error occurred: {str(e)}"}), 500