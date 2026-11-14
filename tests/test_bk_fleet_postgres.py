"""Real database locking test; isolated schema is removed even on failure."""
import os
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

if not os.environ.get('BK_TEST_POSTGRES_URL'):
    raise unittest.SkipTest('isolated PostgreSQL URL required')

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from executor.models import Base, Device, Job, Task, User, UserDevice
from server.bk_ai_store import AiTask
from server.bk_fleet_store import create_job, materialize_due
from server.bk_plan import validate_plan


class ConcurrentCreationTests(unittest.TestCase):
    def test_simultaneous_retry_creates_one_parent(self):
        schema = 'bk_fleet_test_' + uuid.uuid4().hex
        admin = create_engine(os.environ['BK_TEST_POSTGRES_URL'])
        engine = None
        schema_created = False
        try:
            with admin.begin() as connection:
                connection.execute(text('CREATE SCHEMA ' + schema))
            schema_created = True
            engine = create_engine(os.environ['BK_TEST_POSTGRES_URL'],
                                   connect_args={'options': '-csearch_path=' + schema})
            Base.metadata.create_all(engine)
            sessions = sessionmaker(engine, expire_on_commit=False)
            with sessions.begin() as session:
                session.add(User(id=1, name='owner'))
                session.add(Device(id=1, domain='phone1', state='online'))
                session.add(UserDevice(user_id=1, device_id=1))
            plan = validate_plan({'goal': '查询状态', 'target': {'kind': 'device', 'name': 'phone1'},
                                  'schedule': {'kind': 'now'}},
                                 [{'domain': 'phone1'}], [])
            request = str(uuid.uuid4())
            gate = threading.Barrier(2)
            def submit():
                gate.wait(timeout=5)
                with sessions.begin() as session:
                    job, created = create_job(session, 1, request, plan, now=1800000000)
                    return job.id, created
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(submit) for _ in range(2)]
                outcomes = [future.result(timeout=15) for future in futures]
            self.assertEqual(len({outcome[0] for outcome in outcomes}), 1)
            self.assertEqual(sorted(outcome[1] for outcome in outcomes), [False, True])
            with sessions() as session:
                self.assertEqual(session.query(Job).count(), 1)
            job_id = outcomes[0][0]
            gate = threading.Barrier(2)
            def dispatch():
                gate.wait(timeout=5)
                with sessions.begin() as session:
                    return materialize_due(session, job_id, now=1800000001)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(dispatch) for _ in range(2)]
                dispatches = [future.result(timeout=15) for future in futures]
            self.assertEqual(sorted(len(result) for result in dispatches), [0, 1])
            with sessions() as session:
                self.assertEqual(session.query(Task).count(), 1)
                self.assertEqual(session.query(AiTask).count(), 1)
        finally:
            if engine:
                engine.dispose()
            if schema_created:
                with admin.begin() as connection:
                    connection.execute(text('DROP SCHEMA ' + schema + ' CASCADE'))
            admin.dispose()


if __name__ == '__main__':
    unittest.main()
