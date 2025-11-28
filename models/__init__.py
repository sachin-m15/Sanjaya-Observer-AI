"""
Models package for Learning Observer Flask Application.

This package contains all the data models and database interaction classes.
"""

# Import the database module first
from . import database

# Import all functions from database
from .database import (
    init_supabase,
    get_supabase_client,
    get_user_by_id,
    authenticate_user,
    create_user,
    get_children,
    get_observers,
    get_parents,
    get_observer_children,
    save_observation,
    save_processed_data,
    get_observations_by_child,
    get_goals_by_child,
    save_goal,
    get_messages_between_users,
    save_message,
    upload_file_to_storage,
    User
)

# Import other modules
from . import observation_extractor
from .observation_extractor import ObservationExtractor

from . import monthly_report_generator
from .monthly_report_generator import MonthlyReportGenerator

from . import transcript_manager
from .transcript_manager import TranscriptManager

__all__ = [
    # Database module and functions
    'database',
    'init_supabase',
    'get_supabase_client',
    'get_user_by_id',
    'authenticate_user',
    'create_user',
    'get_children',
    'get_observers',
    'get_parents',
    'get_observer_children',
    'save_observation',
    'save_processed_data',
    'get_observations_by_child',
    'get_goals_by_child',
    'save_goal',
    'get_messages_between_users',
    'save_message',
    'upload_file_to_storage',
    'User',
    # Other modules
    'observation_extractor',
    'ObservationExtractor',
    'monthly_report_generator',
    'MonthlyReportGenerator',
    'transcript_manager',
    'TranscriptManager'
]
