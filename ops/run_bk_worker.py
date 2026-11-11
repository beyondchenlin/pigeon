"""Persistent pilot launcher for the compiled PIGEON image."""
import sys
import server
server.__path__.append('/user/phone-pilot/bk-server')
from server.bk_ai_store import AiTask, install_script_guard
from executor.models import engine
AiTask.__table__.create(engine, checkfirst=True)
install_script_guard()
from executor.celery import app
app.worker_main(['worker'] + sys.argv[1:])
