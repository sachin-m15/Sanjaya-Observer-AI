import pandas as pd
import os
from datetime import datetime
import logging
import json

logger = logging.getLogger(__name__)

class TranscriptManager:
    def __init__(self):
        self.transcripts_file = 'transcripts.xlsx'
        self.columns = ['Date', 'Student Name', 'Transcript']

    def save_transcript(self, date, student_name, transcript):
        """Save a transcript to the Excel file"""
        try:
            # Create data for new row
            new_data = {
                'Date': [date],
                'Student Name': [student_name],
                'Transcript': [transcript]
            }

            df_new = pd.DataFrame(new_data)

            # Check if file exists
            if os.path.exists(self.transcripts_file):
                # Read existing data
                df_existing = pd.read_excel(self.transcripts_file)
                # Append new data
                df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            else:
                # Create new file
                df_combined = df_new

            # Save to Excel
            df_combined.to_excel(self.transcripts_file, index=False)
            logger.info(f"Transcript saved for student {student_name} on {date}")

        except Exception as e:
            logger.error(f"Error saving transcript: {str(e)}")
            raise

    def get_transcripts_from_supabase(self, org_id=None):
        """Get all transcripts from Supabase observations table"""
        try:
            from models.database import get_supabase_client

            supabase = get_supabase_client()

            # Build query
            query = supabase.table('observations').select("""
                id, student_name, date, timestamp, filename, full_data, username
            """)

            # If org_id is provided, filter by organization
            if org_id:
                # Get users from this organization
                org_users_response = supabase.table('users').select('id').eq('organization_id', org_id).execute()
                org_user_ids = [user['id'] for user in org_users_response.data] if org_users_response.data else []
                if org_user_ids:
                    query = query.in_('username', org_user_ids)
                else:
                    # No users in organization, return empty
                    return pd.DataFrame(columns=['Date', 'Student Name', 'Transcript', 'Timestamp', 'Filename'])

            # Execute query
            response = query.execute()
            observations = response.data if response.data else []

            # Extract transcripts from full_data JSON
            transcripts_data = []
            for obs in observations:
                try:
                    full_data = json.loads(obs.get('full_data', '{}'))
                    transcript = full_data.get('transcript', '')

                    # Only include if transcript exists and is not empty
                    if transcript and transcript.strip():
                        transcripts_data.append({
                            'Date': obs.get('date', ''),
                            'Student Name': obs.get('student_name', 'Unknown'),
                            'Transcript': transcript,
                            'Timestamp': obs.get('timestamp', ''),
                            'Filename': obs.get('filename', 'N/A')
                        })
                except (json.JSONDecodeError, TypeError):
                    continue

            # Convert to DataFrame
            df = pd.DataFrame(transcripts_data)

            # Sort by timestamp (newest first)
            if not df.empty and 'Timestamp' in df.columns:
                df = df.sort_values('Timestamp', ascending=False)

            return df

        except Exception as e:
            logger.error(f"Error fetching transcripts from Supabase: {str(e)}")
            return pd.DataFrame(columns=['Date', 'Student Name', 'Transcript', 'Timestamp', 'Filename'])

    def get_transcripts_dataframe(self):
        """Get all transcripts as a pandas DataFrame (legacy method)"""
        try:
            if os.path.exists(self.transcripts_file):
                return pd.read_excel(self.transcripts_file)
            else:
                # Return empty DataFrame with correct columns
                return pd.DataFrame(columns=self.columns)
        except Exception as e:
            logger.error(f"Error reading transcripts file: {str(e)}")
            return pd.DataFrame(columns=self.columns)

    def get_transcripts_excel_bytes(self, org_id=None):
        """Get transcripts as Excel file bytes for download from Supabase"""
        try:
            df = self.get_transcripts_from_supabase(org_id)
            from io import BytesIO
            output = BytesIO()
            df.to_excel(output, index=False, engine='openpyxl')
            output.seek(0)
            return output
        except Exception as e:
            logger.error(f"Error creating Excel bytes: {str(e)}")
            raise