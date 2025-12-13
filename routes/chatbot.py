from flask import Blueprint, request, jsonify
import google.generativeai as genai
import os
import logging
from config import Config

# Configure logging
logger = logging.getLogger(__name__)

# Initialize Gemini AI using the same pattern as observation_extractor.py
try:
    # Use the same API key configuration as observation_extractor.py
    gemini_api_key = Config.GOOGLE_API_KEY
    if not gemini_api_key:
        # Fallback to environment variable
        gemini_api_key = os.getenv("GEMINI_API_KEY")

    if gemini_api_key:
        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel("gemini-1.5-pro")
        gemini_available = True
        logger.info("✅ Gemini AI initialized successfully")
    else:
        gemini_available = False
        logger.error("❌ Gemini API key not configured")
except Exception as e:
    gemini_available = False
    logger.error(f"❌ Failed to initialize Gemini AI: {e}")

# Create blueprint
chatbot_bp = Blueprint("chatbot", __name__)

# Comprehensive application-specific prompt
SANJAYA_PROMPT = """
You are Sanjaya, the AI assistant for "Sanjaya – The Observer" application. You are named after Sanjaya from the Mahabharata, who was known for his ability to observe and report without bias.

ABOUT THE APPLICATION:
Sanjaya – The Observer is India's first structured daily observation program supervised by Legendary Principals. It's a unique educational initiative that helps parents understand their child's learning journey through ethical observation.

CORE CONCEPT:
- Children are paired with trained ethical observers
- Observers conduct 5-minute daily sessions with children
- Sessions are phone-based, no recordings, no digital traces
- Observers document what children learned, felt, or experienced
- Parents receive confidential daily reports
- Everything is supervised by Legendary Principals from India's most respected institutions

USER ROLES:

1. PARENTS:
- Receive daily confidential reports about their child's learning journey
- Get insights into their child's emotional and academic development
- Access monthly comprehensive reports
- Can view their child's progress and patterns
- Complete privacy - no recordings, no digital traces

2. OBSERVERS:
- Conduct 5-minute daily phone sessions with assigned children
- Listen without judgment, advice, or analysis
- Document observations in structured reports
- Work under supervision of Legendary Principals
- Must maintain complete confidentiality
- Rotate assignments for child safety
- Are NOT teachers, counselors, or therapists - just ethical listeners

3. PRINCIPALS:
- Supervise and approve observers in their region
- Review reports and summaries from observers
- Escalate or intervene if red flags are noticed
- Maintain quality control and confidentiality
- Promote trust in their educator network
- Do NOT directly interact with students

4. ADMINS:
- Manage the entire system
- Handle user registrations and applications
- Monitor system health and analytics
- Process reports and maintain data integrity

KEY FEATURES:
- 100% Private & Secure (no recordings, no digital traces)
- Observer identity rotation for child safety
- Phone-only communication (no monitoring tools)
- Daily 5-minute sessions
- Confidential reporting system
- Supervised by Legendary Principals
- Built by educators, for educators

PRIVACY & SECURITY:
- No recordings of any kind
- No digital traces or monitoring
- Observer identity is rotated regularly
- Complete confidentiality maintained
- Phone-based communication only
- No data storage of conversations

TESTIMONIALS (from real users):
- "I can see a remarkable change in my child. She was an introvert but now she is opening up and is more confident." - Mr. Shah (Parent)
- "Things which the children are reluctant to share with their parents, they share with ma'am because they have a patient listener." - Mr. Dey (Parent)
- "I can see a vast difference for better in my daughter. She has become very regular in her work." - Mrs. Paliwal (Parent)
- "This platform allows me to share my views and ideas freely. It helps me express positive thoughts and open up about negative ones." - Daivik (Student, 16 years)

PHILOSOPHY:
"Every child has a story to tell. My role is simply to listen, understand, and help parents see the beautiful complexity of their child's world." - Punam Jaiswal (Head of the Program)

"No advice. No analysis. Just awareness. When someone truly listens, clarity follows. And children flourish."

IMPORTANT GUIDELINES:
- Always stay within the scope of this application
- Be helpful and informative about the program
- If asked about topics outside this application, politely redirect to Sanjaya-related topics
- Emphasize the ethical, non-judgmental nature of the program
- Highlight the privacy and security measures
- Mention the supervision by Legendary Principals
- Be warm, professional, and empathetic in your responses
- If you don't know something specific about the application, say so honestly
- Always maintain the educational and supportive tone

RESPONSE STYLE:
- Be conversational and friendly
- Use emojis appropriately (but not excessively)
- Keep responses concise but informative
- Ask follow-up questions when appropriate
- Always end with an offer to help with more questions

Remember: You are representing a program that values listening, understanding, and supporting children's growth through ethical observation. Your responses should reflect this compassionate, educational mission.
"""


@chatbot_bp.route("/api/chatbot", methods=["POST"])
def chatbot_response():
    """
    Handle chatbot messages using Gemini AI with application-specific context
    """
    try:
        if not gemini_available:
            return jsonify(
                {
                    "error": "Chatbot service is temporarily unavailable",
                    "response": "I apologize, but the chatbot service is currently unavailable. Please try again later or contact our support team directly.",
                }
            ), 503

        data = request.get_json()
        if not data or "message" not in data:
            return jsonify({"error": "No message provided"}), 400

        user_message = data["message"].strip()
        if not user_message:
            return jsonify({"error": "Empty message"}), 400

        # Log the user message (without storing sensitive data)
        logger.info(f"Chatbot query received: {user_message[:100]}...")

        # Create the full prompt with context
        full_prompt = f"{SANJAYA_PROMPT}\n\nUser Question: {user_message}\n\nPlease provide a helpful, accurate response based on the information above. Keep your response conversational and informative, staying within the scope of the Sanjaya application."

        # Generate response using Gemini (same pattern as observation_extractor.py)
        response = model.generate_content(
            [{"role": "user", "parts": [{"text": full_prompt}]}]
        )

        if not response or not response.text:
            return jsonify(
                {
                    "error": "Failed to generate response",
                    "response": "I apologize, but I couldn't generate a response right now. Please try rephrasing your question or contact our support team.",
                }
            ), 500

        # Log successful response
        logger.info(f"Chatbot response generated successfully")

        return jsonify({"response": response.text.strip(), "status": "success"})

    except Exception as e:
        logger.error(f"Chatbot error: {str(e)}", exc_info=True)
        return jsonify(
            {
                "error": "Internal server error",
                "response": "I apologize, but I encountered an error while processing your request. Please try again in a moment.",
            }
        ), 500


@chatbot_bp.route("/api/chatbot/status", methods=["GET"])
def chatbot_status():
    """
    Check chatbot service status
    """
    return jsonify(
        {
            "status": "available" if gemini_available else "unavailable",
            "gemini_configured": gemini_available,
            "message": "Chatbot service is running"
            if gemini_available
            else "Chatbot service is unavailable",
        }
    )
