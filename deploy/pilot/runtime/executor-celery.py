import server
server.__path__.append('/user/phone-pilot/bk-server')
# Copyright 2025 rev1si0n (ihaven0emmail@gmail.com). All rights reserved.
# encoding=utf8
from celery import Celery

app = Celery("executor")
app.config_from_object("executor.config.Celery")
app.autodiscover_tasks()


# Keep the device mutex active in workers as well as the HTTP process.
from celery.signals import worker_init


@worker_init.connect
def install_bk_ai_guard(**kwargs):
    from server.bk_ai_store import AiTask, install_script_guard
    from .models import engine
    AiTask.__table__.create(engine, checkfirst=True)
    install_script_guard()
