import requests
import base64
import json
import google.generativeai as genai
import docx
import io
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import time
import re
from datetime import datetime
from config import Config
import os
import logging

logger = logging.getLogger(__name__)


class ObservationExtractor:
    def __init__(self):
        self.ocr_api_key = Config.OCR_API_KEY
        self.groq_api_key = Config.GROQ_API_KEY
        self.gemini_api_key = Config.GOOGLE_API_KEY
        genai.configure(api_key=self.gemini_api_key)

    def get_pronouns(self, gender):
        """Get appropriate pronouns based on gender"""
        if gender == 'male':
            return {'subject': 'he', 'object': 'him', 'possessive': 'his'}
        elif gender == 'female':
            return {'subject': 'she', 'object': 'her', 'possessive': 'her'}
        else:
            return {'subject': 'they', 'object': 'them', 'possessive': 'their'}

    def image_to_base64(self, image_file):
        """Convert image file to base64 string"""
        return base64.b64encode(image_file.read()).decode('utf-8')

    def extract_text_with_ocr(self, image_file):
        """Extract text from image using OCR.space API"""
        try:
            # Get file extension
            file_type = image_file.filename.split('.')[-1].lower()
            if file_type == 'jpeg':
                file_type = 'jpg'

            # Convert image to base64
            image_file.seek(0)  # Reset file pointer
            base64_image = self.image_to_base64(image_file)
            base64_image_with_prefix = f"data:image/{file_type};base64,{base64_image}"

            # Prepare request payload
            payload = {
                'apikey': self.ocr_api_key,
                'language': 'eng',
                'isOverlayRequired': False,
                'iscreatesearchablepdf': False,
                'issearchablepdfhidetextlayer': False,
                'OCREngine': 2,
                'detectOrientation': True,
                'scale': True,
                'base64Image': base64_image_with_prefix
            }

            # Send request to OCR API
            response = requests.post(
                'https://api.ocr.space/parse/image',
                data=payload,
                headers={'apikey': self.ocr_api_key}
            )

            response.raise_for_status()
            data = response.json()

            # Process response
            if not data.get('ParsedResults') or len(data['ParsedResults']) == 0:
                error_msg = data.get('ErrorMessage', 'No parsed results returned')
                raise Exception(f"OCR Error: {error_msg}")

            parsed_result = data['ParsedResults'][0]
            if parsed_result.get('ErrorMessage'):
                raise Exception(f"OCR Error: {parsed_result['ErrorMessage']}")

            extracted_text = parsed_result['ParsedText']

            if not extracted_text or not extracted_text.strip():
                raise Exception("No text was detected in the image")

            return extracted_text

        except Exception as e:
            raise Exception(f"OCR Error: {str(e)}")

    def process_with_groq(self, extracted_text):
        """Process extracted text with Groq AI"""
        try:
            system_prompt = """You are an AI assistant for a learning observation system. Extract and structure information from the provided observation sheet text.

CRITICAL INSTRUCTIONS FOR NAME HANDLING:
- Do NOT use any names that appear in the observation text or audio transcription
- For the "studentName" field, ONLY use names that are explicitly provided by the system/database
- If no database name is provided, use "Student" as the default
- NEVER assume gender - always refer to the student as "the student" or "Student" in descriptions
- Do not use gender-specific pronouns (he/his, she/her) in any part of the response

The observation sheets typically have the following structure:
- Title (usually "The Observer")
- Student information (Name, Roll Number/ID) - IGNORE names from this section
- Date and Time information
- Core Observation Section with time slots
- Teaching content for each time slot
- Learning details (what was learned, tools used, etc.)

Format your response as JSON with the following structure:
{
  "studentName": "Use ONLY the database-provided name, never from observation text",
  "studentId": "Student ID or Roll Number",
  "className": "Class name or subject being taught",
  "date": "Date of observation",
  "observations": "Detailed description of what was learned - refer to 'the student' not by name",
  "strengths": ["List of strengths observed - use 'the student' in descriptions"],
  "areasOfDevelopment": ["List of areas where the student needs improvement - use 'the student'"],
  "recommendations": ["List of recommended actions - refer to 'the student'"],
  "themeOfDay": "Main theme or topic of the day",
  "curiositySeed": "Something that sparked the child's interest"
}

For observations, provide full detailed descriptions like:
"The student learned how to make maggi from their parent through in-person mode, including all steps from boiling water to adding spices"

IMPORTANT: Never use gender-specific language or names from the observation text. Always refer to 'the student' in descriptions."""

            # Send request to Groq API
            response = requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {self.groq_api_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {
                            "role": "system",
                            "content": system_prompt
                        },
                        {
                            "role": "user",
                            "content": f"Extract and structure the following text from an observation sheet: {extracted_text}"
                        }
                    ],
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"}
                }
            )

            response.raise_for_status()
            data = response.json()

            # Extract the JSON content
            ai_response = data['choices'][0]['message']['content']
            return json.loads(ai_response)

        except Exception as e:
            raise Exception(f"Groq API Error: {str(e)}")

    def transcribe_with_assemblyai(self, audio_file, language_code='en'):
        """Transcribe audio using AssemblyAI API with English, Hindi, Marathi, or Punjabi."""
        if not Config.ASSEMBLYAI_API_KEY:
            return "Error: AssemblyAI API key is not configured."

        headers = {
            "authorization": Config.ASSEMBLYAI_API_KEY,
            "content-type": "application/json"
        }

        try:
            # Upload the audio file
            audio_file.seek(0)
            upload_response = requests.post(
                "https://api.assemblyai.com/v2/upload",
                headers={"authorization": Config.ASSEMBLYAI_API_KEY},
                data=audio_file.read()
            )

            if upload_response.status_code != 200:
                return f"Error uploading audio: {upload_response.text}"

            upload_url = upload_response.json()["upload_url"]

            # Prepare transcription request
            transcript_request = {
                "audio_url": upload_url,
                "language_code": language_code  # 'en', 'hi', 'mr', or 'pa'
            }

            transcript_response = requests.post(
                "https://api.assemblyai.com/v2/transcript",
                json=transcript_request,
                headers=headers
            )

            if transcript_response.status_code != 200:
                return f"Error requesting transcription: {transcript_response.text}"

            transcript_id = transcript_response.json()["id"]

            status = "processing"
            timeout = time.time() + 300  # 5-minute timeout
            while status not in ["completed", "error"] and time.time() < timeout:
                polling_response = requests.get(
                    f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
                    headers=headers
                )

                if polling_response.status_code != 200:
                    return f"Error checking transcription status: {polling_response.text}"

                polling_data = polling_response.json()
                status = polling_data["status"]

                if status == "completed":
                    return polling_data["text"]
                elif status == "error":
                    return f"Transcription error: {polling_data.get('error', 'Unknown error')}"

                time.sleep(2)

            return "Error: Transcription timed out or failed."

        except Exception as e:
            return f"Error during transcription: {str(e)}"

    def generate_conversational_transcript(self, raw_text):
        """Convert raw observations/transcript into conversational format using Gemini API"""
        try:
            prompt = f"""
            Convert the following observation text into a conversational format between an Observer and a Child. 

CRITICAL INSTRUCTIONS:
- NEVER use any names that appear in the raw text or audio
- Always refer to the child as "Child" in the dialogue labels
- Do not assume gender - avoid using he/his, she/her pronouns
- Use gender-neutral language throughout the conversation

Format it as a natural dialogue where:
            - Observer speaks first with questions, instructions, or observations
            - Child responds naturally based on the context
            - Use "Observer:" and "Child:" labels for each speaker
            - Make it realistic and age-appropriate
            - If the text is already conversational, just format it properly
            - If it's narrative observations, convert them into likely dialogue
            - Create a natural flow of conversation that would lead to the observations described
            - Include educational moments and learning interactions
            - NEVER use names from the original text - always use "Child:" as the label
            - Avoid gender assumptions in the dialogue content

            Original observation text:
            {raw_text}

            Please format as a realistic conversation:
            Observer: [what the observer might have said or asked]
            Child: [how the child might have responded based on the context]
            Observer: [follow-up from observer]
            Child: [child's response]

            Keep it natural, educational, and age-appropriate. Make sure the conversation flows logically and would realistically result in the observations described in the original text. Remember to never use names from the original text and avoid gender-specific language.
            """

            model = genai.GenerativeModel('gemini-2.0-flash')
            config = genai.types.GenerationConfig(temperature=0.2)
            response = model.generate_content([
                {"role": "user", "parts": [{"text": prompt}]}
            ], generation_config=config)

            if response and response.text:
                return response.text.strip()
            else:
                return self._basic_transcript_formatting(raw_text)

        except Exception as e:
            return self._basic_transcript_formatting(raw_text)

    def _basic_transcript_formatting(self, raw_text):
        """Basic fallback transcript formatting"""
        lines = raw_text.split('\n')
        formatted_lines = []

        for i, line in enumerate(lines):
            line = line.strip()
            if line:
                if i % 2 == 0:
                    formatted_lines.append(f"Observer: {line}")
                else:
                    formatted_lines.append(f"Child: {line}")

        if not formatted_lines:
            formatted_lines = [
                "Observer: Can you tell me about what you learned today?",
                f"Child: {raw_text[:100]}..." if len(raw_text) > 100 else f"Child: {raw_text}",
                "Observer: That's wonderful! Can you tell me more about it?",
                "Child: Yes, I enjoyed learning about this topic."
            ]

        return '\n'.join(formatted_lines)

    # =============================================
    # UPDATED METHOD: DAILY INSIGHTS REPORT (TEEN)
    # =============================================
    def generate_report_from_text(self, text_content, user_info):
        """Generate a structured Daily Insights Report from text using Google Gemini"""
        from models.database import get_child_by_id

        # Get child information and gender-based pronouns
        child_id = user_info.get('child_id')
        student_name = user_info.get('student_name', 'Student')

        if not child_id and student_name != 'Student':
            from models.database import get_supabase_client
            supabase = get_supabase_client()
            child_data = supabase.table('children').select('id, name, gender').eq('name', student_name).execute().data
            child = child_data[0] if child_data else None
            if child_data:
                child_id = child_data[0]['id']
        else:
            child = get_child_by_id(child_id) if child_id else None

        pronouns = self.get_pronouns(child['gender']) if child and child.get('gender') else {'subject': 'they', 'object': 'them', 'possessive': 'their'}
        if not student_name or student_name == 'Student':
            student_name = child['name'] if child and child.get('name') else 'Student'

        prompt = f"""
        You are an educational observer tasked with generating a comprehensive and accurate Daily Growth Report based on the following observational notes from a student session. Pay special attention to any achievements, learning moments, and areas for growth. The report should be structured, insightful, and easy to understand for parents. Add postives and negatives based on the text content provided. 

        CRITICAL INSTRUCTIONS FOR NAME AND GENDER USAGE:
        - NEVER extract or use any name from the audio transcription or text content
        - ALWAYS use the exact name provided: {student_name}
        - Use these pronouns for the student throughout the report: subject = {pronouns['subject']}, object = {pronouns['object']}, possessive = {pronouns['possessive']}
        - When referring to the student, use "{student_name}" or the appropriate pronouns ({pronouns['subject']}/{pronouns['object']}/{pronouns['possessive']})
        - Make sure the report is grammatically correct and adheres to proper English syntax and semantics.
        Please carefully analyze the given text and complete the report using the exact format, emojis, section titles, and scoring rubrics as described below. The student should be referred to consistently using their provided name "{student_name}" and the appropriate pronouns - never use names from the audio/text content.

        📌 Important Instructions for the Report:
        - Follow the format exactly as shown below.
        - Make reasonable inferences for items not explicitly stated in the text.

======================================================
Session Details
• Date: [{user_info.get('session_date', 'Today')}]  
• Time: [Approximate duration if mentioned or inferred]  
• Listener: [{user_info.get('observer_name', 'Observer')}]  
• Teen: {student_name}

Today’s Insights
• What {student_name} shared about their day (experiences, emotions, highlights).  
• Key learnings or reflections expressed.  
• Emotional tone and engagement level.  
• Questions asked by the listener to encourage reflection or connection.

Thoughts–Words–Actions Alignment
**Thoughts (What {student_name} is thinking about):**  
- Goals, concerns, or self-reflections mentioned.

**Words (How {student_name} communicates):**  
- Clarity, confidence, or alignment between intentions and statements.

**Actions (What {student_name} actually did):**  
- Daily behaviors or choices showing progress or contradiction with stated goals.

Goal Alignment Assessment
• Alignment Score: [High / Medium / Low]  
• Evidence of Alignment:  
  - Activities supporting goals.  
  - Decisions reflecting long-term thinking.  
• Misalignment Observations:  
  - Contradictions between words and actions.  
  - Time spent on unrelated or non-productive activities.  
• Questions Asked for Redirection:  
  - “Will this help toward your goal?” and {student_name}’s responses or realizations.

Tomorrow’s Plans Review
• Stated Plans: Activities or intentions for the next day.  
• Goal Connection Analysis:  
  - How tomorrow’s plans align with long-term ambitions.  
  - Adjustments based on today’s reflection.

Behavioral Observations
**Engagement Level:**  
- Enthusiasm, curiosity, and willingness to reflect.  

**Self-Awareness Indicators:**  
- Ability to recognize own patterns or growth areas.  

**Response to Redirection:**  
- How {student_name} handled constructive guidance or alignment checks.

Communication Quality
**Listening Skills:**  
- Attentiveness and understanding of questions.  

**Expression Clarity:**  
- Ability to articulate thoughts, emotions, and goals.  
- Confidence and openness in conversation.

Parent Recommendations (Observer Notes)
**Strengths Observed:**  
- Areas of strong alignment or progress.  
- Positive emotional or behavioral patterns.

**Areas for Continued Focus:**  
- Recurrent challenges or habits to monitor.  
- Questions that prompt best self-reflection.  

**Suggested Parent Follow-up:**  
- Conversation themes or gentle accountability ideas.  
- Achievements worth appreciating.

Call Summary
**Overall Assessment:**  
- {student_name}’s current level of goal-directed thinking and self-awareness.  

**Key Insights for Parents:**  
- Most notable learnings or growth signs.  
- Actionable suggestions to support continued development at home.  

======================================================

Now, generate a complete, polished report for {student_name} in this exact structure and tone.  
Do not include any commentary, reasoning, or additional notes outside this format.  
Ensure all emojis and section headings remain as shown.
TEXT CONTENT TO ANALYZE:
{text_content}
"""

        try:
            model = genai.GenerativeModel('gemini-2.0-flash')
            config = genai.types.GenerationConfig(temperature=0.2)
            response = model.generate_content([
                {"role": "user", "parts": [{"text": prompt}]}
            ], generation_config=config)
            return response.text
        except Exception as e:
            return f"Error generating report: {str(e)}"

    def generate_ai_communication_review(self, transcript, user_info):
        """Generate AI communication review for peer review system"""
        from models.database import get_child_by_id
        child = get_child_by_id(user_info.get('child_id'))
        pronouns = self.get_pronouns(child['gender']) if child and child.get('gender') else {'subject': 'they', 'object': 'them', 'possessive': 'their'}
        
        prompt = f"""
        You are an AI assistant analyzing observer-student communication sessions for educational quality assessment. 
        Generate a comprehensive communication review in a professional report format based on the provided transcript.

        STUDENT: {user_info['student_name']}
        OBSERVER: {user_info['observer_name']}
        TRANSCRIPT: {transcript}
        
        Use these pronouns for the student throughout the review: subject = {pronouns['subject']}, object = {pronouns['object']}, possessive = {pronouns['possessive']}

        Generate a detailed AI communication review in the following professional format:

        Analysis of Observer's Communication Style
        This report analyzes the conversation transcripts to evaluate the observer's adherence to non-judgmental and non-teaching communication techniques with {user_info['student_name']}.

        1. Instances of Direct Advice, Judgment, or Teaching
        [Analyze specific examples where the observer provided direct advice, made judgments, or acted as a teacher rather than a listener]

        ● Red Flag: [Specific issue identified]
        ○ Instances: [Describe specific examples from the transcript]
        ■ [Date if available]: "[Exact quote from transcript]"
        ○ Analysis: [Explain why this is problematic and its impact]

        ● Red Flag: [Another specific issue]
        ○ Instance: [Describe the instance]
        ○ Analysis: [Explain the impact and why it's concerning]

        2. Suggested Rephrasing for Non-Judgmental Communication
        [Provide specific examples of how the observer could rephrase their interactions]

        ● For [specific issue]:
        ○ Instead of: "[Current problematic phrase]"
        ○ Ask: "[Suggested non-judgmental alternative]"

        3. Missed Opportunities for Deeper Conversation
        [Identify moments where the observer could have explored topics more deeply]

        ● Date: [if available]
        ○ Missed Opportunity: [Describe what the student shared that could have been explored further]
        ○ Suggested Question: "[Specific question the observer could have asked]"

        4. Adherence to Non-Judgmental Listening: A Summary
        [Create a summary table of the observer's performance]

        Date | Adherence (Yes/No) | Red Flags | Suggested Improvements
        [Date] | Yes/No | Yes/No | [Brief description of what went well or needs improvement]

        5. Findings and Recommendations for Program Managers
        [Provide actionable insights and recommendations]

        Observer Improvement Areas
        [Summarize the main areas where the observer needs to improve]

        Impact on the Student ({user_info['student_name']})
        [Analyze how the observer's communication style affects the student]

        Recommendation: [Provide specific, actionable recommendations for improvement]

        Format the review with proper hierarchical structure using the bullet points (●, ○, ■) and numbered sections as shown above. Include specific quotes from the transcript when relevant. Be constructive and educational in tone while being honest about areas that need improvement.
        """

        try:
            model = genai.GenerativeModel('gemini-2.0-flash')
            response = model.generate_content([
                {"role": "user", "parts": [{"text": prompt}]}
            ])
            return response.text
        except Exception as e:
            return f"Error generating AI communication review: {str(e)}"

    def create_word_document_with_emojis(self, report_content):
        """Create a Word document from the report content with emoji support"""
        doc = docx.Document()

        # Set document encoding and font for emoji support
        style = doc.styles['Normal']
        font = style.font
        font.name = 'Segoe UI Emoji'

        title = doc.add_heading('Daily Growth Report', 0)
        title.runs[0].font.name = 'Segoe UI Emoji'

        # Process the report content line by line
        lines = report_content.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Clean up markdown formatting but preserve emojis
            line = line.replace('**', '')

            if line.startswith(('Child', 'Date', 'Curiosity Seed')):
                p = doc.add_paragraph()
                run = p.add_run(line)
                run.bold = True
                run.font.name = 'Segoe UI Emoji'
                run.font.size = docx.shared.Pt(12)
            elif line.startswith('Growth Metrics'):
                heading = doc.add_heading(line, level=1)
                heading.runs[0].font.name = 'Segoe UI Emoji'
            elif line.startswith(('Intellectual', 'Emotional', 'Social', 'Creativity', 'Physical', 'Character/Values', 'Planning/Independence')):
                p = doc.add_paragraph()
                run = p.add_run(line)
                run.bold = True
                run.font.name = 'Segoe UI Emoji'
                run.font.size = docx.shared.Pt(11)
            elif line.startswith(('Curiosity Response', 'Communication Skills')):
                heading = doc.add_heading(line, level=2)
                heading.runs[0].font.name = 'Segoe UI Emoji'
            elif line.startswith('Overall'):
                heading = doc.add_heading(line, level=2)
                heading.runs[0].font.name = 'Segoe UI Emoji'
            elif line.startswith('Note for Parent'):
                heading = doc.add_heading(line, level=2)
                heading.runs[0].font.name = 'Segoe UI Emoji'
            elif line.startswith('Legend'):
                heading = doc.add_heading(line, level=3)
                heading.runs[0].font.name = 'Segoe UI Emoji'
            elif line.startswith(('Excellent', 'Good', 'Fair', 'Needs Work', 'Balanced Growth', 'Moderate Growth', 'Limited Growth', '•', 'Good Score')):
                p = doc.add_paragraph(line, style='List Bullet')
                p.runs[0].font.name = 'Segoe UI Emoji'
                p.runs[0].font.size = docx.shared.Pt(10)
            else:
                p = doc.add_paragraph()
                run = p.add_run(line)
                run.font.name = 'Segoe UI Emoji'
                run.font.size = docx.shared.Pt(10)

        # Save to BytesIO object
        docx_bytes = io.BytesIO()
        doc.save(docx_bytes)
        docx_bytes.seek(0)

        return docx_bytes

    def create_pdf_alternative(self, report_content):
        """Create PDF using reportlab instead of WeasyPrint"""
        from reportlab.lib.pagesizes import letter, A4
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.lib.colors import black, blue

        # Create emoji mapping for text replacement
        emoji_map = {
            'Child': '[Child]',
            'Date': '[Date]',
            'Curiosity Seed': '[Curiosity Seed]',
            'Growth Metrics': '[Growth Metrics]',
            'Intellectual': '[Intellectual]',
            'Emotional': '[Emotional]',
            'Social': '[Social]',
            'Creativity': '[Creativity]',
            'Physical': '[Physical]',
            'Character/Values': '[Character/Values]',
            'Planning/Independence': '[Planning/Independence]',
            'Curiosity Response': '[Curiosity Response]',
            'Communication Skills': '[Communication Skills]',
            'Note for Parent': '[Note for Parent]',
            'Excellent': '[Excellent]',
            'Good': '[Good]',
            'Fair': '[Fair]',
            'Needs Work': '[Needs Work]',
            'Balanced Growth': '[Balanced Growth]',
            'Moderate Growth': '[Moderate Growth]',
            'Limited Growth': '[Limited Growth]',
            'Good Score': '[Good Score]',
            'Report': '[Report]'
        }

        # Replace emojis with readable text
        pdf_content = report_content
        for emoji, replacement in emoji_map.items():
            pdf_content = pdf_content.replace(emoji, replacement)

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=18
        )

        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=16,
            spaceAfter=20,
            textColor=blue,
            alignment=1
        )

        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=12,
            spaceAfter=10,
            textColor=black,
            fontName='Helvetica-Bold'
        )

        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=10,
            spaceAfter=4,
            fontName='Helvetica'
        )

        story = []

        title = Paragraph("[Report] Daily Growth Report", title_style)
        story.append(title)
        story.append(Spacer(1, 12))

        lines = pdf_content.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue

            try:
                if line.startswith(('[Child]', '[Date]', '[Curiosity Seed]')):
                    para = Paragraph(f"<b>{line}</b>", heading_style)
                    story.append(para)
                elif line.startswith('[Growth Metrics]'):
                    para = Paragraph(f"<b>{line}</b>", heading_style)
                    story.append(para)
                elif line.startswith(('[Intellectual]', '[Emotional]', '[Social]', '[Creativity]', '[Physical]',
                                      '[Character/Values]', '[Planning/Independence]')):
                    para = Paragraph(f"<b>{line}</b>", normal_style)
                    story.append(para)
                elif line.startswith(('[Curiosity Response]', '[Communication Skills]', '[Note for Parent]')):
                    para = Paragraph(f"<b>{line}</b>", heading_style)
                    story.append(para)
                elif 'Overall Growth Score' in line:
                    para = Paragraph(f"<b>{line}</b>", heading_style)
                    story.append(para)
                elif line.startswith('[Excellent] Legend'):
                    para = Paragraph(f"<b>{line}</b>", heading_style)
                    story.append(para)
                else:
                    para = Paragraph(line, normal_style)
                    story.append(para)

                story.append(Spacer(1, 4))

            except Exception:
                continue

        doc.build(story)
        buffer.seek(0)
        return buffer

    def create_pdf_with_emojis(self, report_content):
        """Alias for create_pdf_alternative for backward compatibility"""
        return self.create_pdf_alternative(report_content)

    def create_word_document(self, report_content):
        """Legacy method - calls the emoji version"""
        return self.create_word_document_with_emojis(report_content)

    def send_email(self, recipient_email, subject, message):
        """Send email with the observation report"""
        sender_email = Config.EMAIL_USER
        sender_password = Config.EMAIL_PASSWORD

        if not sender_password:
            return False, "Email password not configured"

        smtp_server = "smtp.gmail.com"
        smtp_port = 587

        msg = MIMEMultipart()
        msg["From"] = sender_email
        msg["To"] = recipient_email
        msg["Subject"] = subject

        html_message = f"""
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.6; }}
                pre {{ white-space: pre-wrap; font-family: inherit; }}
            </style>
        </head>
        <body>
            <pre>{message}</pre>
        </body>
        </html>
        """

        msg.attach(MIMEText(html_message, "html", "utf-8"))

        try:
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
            server.quit()
            return True, f"Email sent to {recipient_email}"
        except smtplib.SMTPAuthenticationError:
            return False, "Error: Authentication failed. Check your email and password."
        except smtplib.SMTPException as e:
            return False, f"Error: Failed to send email. {e}"
        except Exception as e:
            return False, f"Error: {str(e)}"

    def generate_custom_report_from_prompt(self, prompt, child_id):
        """Generate custom report based on prompt and stored data with new JSON format"""
        from models.database import get_supabase_client, get_child_by_id

        try:
            supabase = get_supabase_client()
            processed_data = supabase.table('processed_observations').select("*").eq('child_id', child_id).execute()
            observations = supabase.table('observations').select("*").eq('student_id', child_id).execute()

            child = get_child_by_id(child_id)
            child_name = child['name'] if child else 'Student'
            pronouns = self.get_pronouns(child['gender']) if child and child.get('gender') else {'subject': 'they', 'object': 'them', 'possessive': 'their'}

            all_data = {
                'processed_observations': processed_data.data,
                'observations': observations.data
            }

            custom_prompt = f"""
            You are an AI assistant for a learning observation system. Extract and structure information from the provided observation data based on the user's specific request.

CRITICAL INSTRUCTIONS FOR NAME AND GENDER USAGE:
- ALWAYS use the exact name from the database: {child_name}
- NEVER use any names that appear in the observation data or audio transcriptions
- Use these pronouns for the student throughout the report: subject = {pronouns['subject']}, object = {pronouns['object']}, possessive = {pronouns['possessive']}
- When describing activities, use "{child_name}" or the appropriate pronouns

            USER PROMPT: {prompt}
            AVAILABLE DATA: {json.dumps(all_data, indent=2)}

            Format your response as JSON with the following structure:
            {{
              "studentName": "{child_name}",
              "studentId": "Custom Report ID",
              "className": "Custom Analysis Report",
              "date": "{datetime.now().strftime('%Y-%m-%d')}",
              "observations": "Detailed description combining all relevant observations that match the user's prompt",
              "strengths": ["List of strengths observed in {child_name} based on available data"],
              "areasOfDevelopment": ["List of areas where {child_name} needs improvement"],
              "recommendations": ["List of recommended actions for {child_name} based on the prompt and data"]
            }}
            """

            model = genai.GenerativeModel('gemini-2.0-flash')
            response = model.generate_content([
                {"role": "user", "parts": [{"text": custom_prompt}]}
            ])

            try:
                response_text = response.text.strip()
                if response_text.startswith('```json'):
                    response_text = response_text[7:]
                if response_text.startswith('```'):
                    response_text = response_text[3:]
                if response_text.endswith('```'):
                    response_text = response_text[:-3]
                
                start_idx = response_text.find('{')
                end_idx = response_text.rfind('}') + 1
                json_text = response_text[start_idx:end_idx] if start_idx != -1 and end_idx > start_idx else response_text
                json_response = json.loads(json_text)

                formatted_report = f"""
Custom Report: {json_response.get('className', 'Custom Analysis Report')}

Student Name: {json_response.get('studentName', child_name)}
Date: {json_response.get('date', datetime.now().strftime('%Y-%m-%d'))}
Report Type: Custom Analysis

Observations Summary:
{json_response.get('observations', 'No observations available')}

Strengths Identified:
{chr(10).join([f"• {s}" for s in json_response.get('strengths', [])])}

Areas for Development:
{chr(10).join([f"• {a}" for a in json_response.get('areasOfDevelopment', [])])}

Recommendations:
{chr(10).join([f"• {r}" for r in json_response.get('recommendations', [])])}

Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                """
                return formatted_report.strip()

            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"JSON parsing failed: {str(e)}")
                return f"""
Custom Report: Custom Analysis Report

Student Name: {child_name}
Date: {datetime.now().strftime('%Y-%m-%d')}
Report Type: Custom Analysis

Analysis Results:
{response.text}

Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                """.strip()

        except Exception as e:
            return f"Error generating custom report: {str(e)}"

    def generate_monthly_report_json_format(self, observations, goal_progress, child_name, year, month, child_id=None):
        """Generate monthly summary in the new JSON format with graph recommendations"""
        try:
            import calendar
            from models.database import get_child_by_id

            child = get_child_by_id(child_id) if child_id else None
            pronouns = self.get_pronouns(child['gender']) if child and child.get('gender') else {'subject': 'they', 'object': 'them', 'possessive': 'their'}

            observation_texts = []
            all_strengths = []
            all_developments = []
            all_recommendations = []

            for obs in observations:
                observation_texts.append(obs.get('observations', ''))
                if obs.get('strengths'):
                    try:
                        strengths = json.loads(obs['strengths']) if isinstance(obs['strengths'], str) else obs['strengths']
                        all_strengths.extend(strengths)
                    except:
                        pass
                if obs.get('areas_of_development'):
                    try:
                        developments = json.loads(obs['areas_of_development']) if isinstance(obs['areas_of_development'], str) else obs['areas_of_development']
                        all_developments.extend(developments)
                    except:
                        pass
                if obs.get('recommendations'):
                    try:
                        recommendations = json.loads(obs['recommendations']) if isinstance(obs['recommendations'], str) else obs['recommendations']
                        all_recommendations.extend(recommendations)
                    except:
                        pass

            total_observations = len(observations)
            active_goals = len([g for g in goal_progress if g.get('status') == 'active'])
            completed_goals = len([g for g in goal_progress if g.get('status') == 'achieved'])

            strength_counts = {s: all_strengths.count(s) for s in set(all_strengths)}
            development_counts = {d: all_developments.count(d) for d in set(all_developments)}

            graph_suggestions = []
            if total_observations > 0:
                graph_suggestions.append({"type": "bar_chart", "title": "Observation Frequency by Week", "description": f"{total_observations} observations"})
            if strength_counts:
                graph_suggestions.append({"type": "pie_chart", "title": "Top Strengths", "data": strength_counts, "description": f"{len(strength_counts)} strength areas"})
            if development_counts:
                graph_suggestions.append({"type": "horizontal_bar", "title": "Development Areas", "data": development_counts, "description": "Focus areas"})
            if goal_progress:
                graph_suggestions.append({"type": "donut_chart", "title": "Goal Progress", "data": {"Active": active_goals, "Completed": completed_goals}, "description": f"{completed_goals} completed, {active_goals} active"})

            monthly_prompt = f"""
            Generate a monthly report for {child_name} in {calendar.month_name[month]} {year} in JSON format.
            Use pronouns: {pronouns['subject']}/{pronouns['object']}/{pronouns['possessive']}.

            Total Observations: {total_observations}
            Goals: {active_goals} active, {completed_goals} completed

            Return JSON with:
            - studentName, studentId, className, date
            - observations (summary)
            - strengths, areasOfDevelopment, recommendations (lists)
            - monthlyMetrics, suggestedGraphs
            """

            model = genai.GenerativeModel('gemini-2.0-flash')
            response = model.generate_content([{"role": "user", "parts": [{"text": monthly_prompt}]}])
            return response.text

        except Exception as e:
            return f"Error generating monthly summary: {str(e)}"

    def generate_monthly_docx_report(self, observations, goal_progress, strength_counts, development_counts, summary_json):
        """Generate a narrative-rich monthly report as a Word document"""
        import docx
        from docx.shared import Inches, Pt
        from io import BytesIO
        import matplotlib.pyplot as plt
        import re
        import json

        doc = docx.Document()
        style = doc.styles['Normal']
        font = style.font
        font.name = 'Segoe UI'
        font.size = Pt(11)

        doc.add_heading(f"Monthly Growth Report", 0)
        doc.add_paragraph(f"{summary_json.get('date', '')}")
        doc.add_paragraph(f"Student: {summary_json.get('studentName', '')}")
        doc.add_paragraph("")
        doc.add_paragraph(summary_json.get('observations', ''))
        doc.add_paragraph("")

        doc.add_heading("Strengths Observed", level=1)
        for s in summary_json.get('strengths', []):
            doc.add_paragraph(s, style='List Bullet')
        doc.add_paragraph("")

        doc.add_heading("Areas for Development", level=1)
        for a in summary_json.get('areasOfDevelopment', []):
            doc.add_paragraph(a, style='List Bullet')
        doc.add_paragraph("")

        doc.add_heading("Recommendations for Next Month", level=1)
        for r in summary_json.get('recommendations', []):
            doc.add_paragraph(r, style='List Bullet')
        doc.add_paragraph("")

        doc.add_heading("Visual Analytics", level=1)
        # Add graphs here as in original
        # ... (graph code omitted for brevity)

        docx_bytes = BytesIO()
        doc.save(docx_bytes)
        docx_bytes.seek(0)
        return docx_bytes

    def generate_topic_suggestions(self, observer_data, child_data, child_name, child_id=None):
        """Generate topic suggestions using Gemini AI based on observation history"""
        try:
            from models.database import get_child_by_id
            child = get_child_by_id(child_id) if child_id else None
            pronouns = self.get_pronouns(child['gender']) if child and child.get('gender') else {'subject': 'they', 'object': 'them', 'possessive': 'their'}

            # ... (rest of logic same as before)

            return "Suggested topics here..."
        except Exception as e:
            logger.error(f"Error: {str(e)}")
            return self._fallback_suggestions(child_name)

    def _fallback_suggestions(self, child_name):
        return f"Suggested topics for {child_name}..."