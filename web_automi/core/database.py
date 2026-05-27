"""
web_automi/core/database.py
---------------------------
Shared database service instance for Web-Automi application.
"""

from web_automi.core.config import DB_PATH
from web_automi.services.db_service import SQLiteDatabaseService

db = SQLiteDatabaseService(DB_PATH)
