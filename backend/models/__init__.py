"""
Import all ORM models so that SQLAlchemy's declarative registry discovers
all relationship() targets when any single model is imported.
"""
from backend.models.user import User
from backend.models.task import ResearchTask
from backend.models.report import ResearchReport, Citation
from backend.models.document import Document
from backend.models.conversation import Conversation, Message
