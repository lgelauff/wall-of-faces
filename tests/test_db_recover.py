"""
Tests for db.recover_stale_jobs.

recover_stale_jobs() is called once on startup to reset jobs that were
in-flight when the process was killed. It must:

  GatherQueue   — delete rows with status 'waiting' or 'running'.
                  Completed ('done'/'error') rows must be preserved.
  SnapshotQueue — mark rows with status 'waiting' or 'running' as 'error'.
                  Completed rows must be preserved.
  UserProfile   — reset gather_status from 'running'/'queued' to 'idle'
                  and gather_progress to 0. Profiles at 'done'/'error'/'idle'
                  must not be changed.
"""

import pytest
from src.db import GatherQueue, SnapshotQueue, UserProfile, db as _db, recover_stale_jobs
from src.utils import utcnow
from conftest import TEST_USERNAME


class TestRecoverStaleJobs:

    def test_running_gather_queue_rows_deleted(self, flask_app):
        """In-flight GatherQueue rows must be removed so the user can re-trigger."""
        with flask_app.app_context():
            _db.session.add(GatherQueue(username='RunningUser', status='running'))
            _db.session.add(GatherQueue(username='WaitingUser', status='waiting'))
            _db.session.commit()

            recover_stale_jobs()

            assert GatherQueue.query.filter_by(username='RunningUser').first() is None
            assert GatherQueue.query.filter_by(username='WaitingUser').first() is None

    def test_completed_gather_queue_rows_preserved(self, flask_app):
        """Finished GatherQueue rows must not be touched."""
        with flask_app.app_context():
            _db.session.add(GatherQueue(username='DoneUser',  status='done'))
            _db.session.add(GatherQueue(username='ErrorUser', status='error'))
            _db.session.commit()

            recover_stale_jobs()

            assert GatherQueue.query.filter_by(username='DoneUser').first()  is not None
            assert GatherQueue.query.filter_by(username='ErrorUser').first() is not None

    def test_running_snapshot_queue_rows_marked_error(self, flask_app):
        """
        In-flight SnapshotQueue rows must be marked 'error' rather than deleted
        — a partial snapshot directory may exist on disk, and the admin needs
        to see it to decide whether to re-trigger or clean up manually.
        """
        with flask_app.app_context():
            _db.session.add(SnapshotQueue(username='RunningSnap', status='running'))
            _db.session.add(SnapshotQueue(username='WaitingSnap', status='waiting'))
            _db.session.commit()

            recover_stale_jobs()

            row_running = SnapshotQueue.query.filter_by(username='RunningSnap').first()
            row_waiting = SnapshotQueue.query.filter_by(username='WaitingSnap').first()
            assert row_running is not None and row_running.status == 'error'
            assert row_waiting is not None and row_waiting.status == 'error'

    def test_completed_snapshot_queue_rows_preserved(self, flask_app):
        """Finished SnapshotQueue rows must not be changed."""
        with flask_app.app_context():
            _db.session.add(SnapshotQueue(username='DoneSnap',  status='done'))
            _db.session.add(SnapshotQueue(username='ErrorSnap', status='error'))
            _db.session.commit()

            recover_stale_jobs()

            row_done  = SnapshotQueue.query.filter_by(username='DoneSnap').first()
            row_error = SnapshotQueue.query.filter_by(username='ErrorSnap').first()
            assert row_done  is not None and row_done.status  == 'done'
            assert row_error is not None and row_error.status == 'error'

    def test_stale_profile_gather_status_reset(self, flask_app, profile):
        """
        A profile stuck in 'running' or 'queued' must be reset to 'idle' with
        gather_progress=0 so the user sees the "start gathering" prompt again.
        """
        with flask_app.app_context():
            user = UserProfile.query.filter_by(username=TEST_USERNAME).first()
            user.gather_status   = 'running'
            user.gather_progress = 75
            _db.session.commit()

            recover_stale_jobs()

            user = UserProfile.query.filter_by(username=TEST_USERNAME).first()
            assert user.gather_status   == 'idle'
            assert user.gather_progress == 0

    def test_idle_profile_not_modified(self, flask_app, profile):
        """Profiles already at 'idle' must not be touched."""
        with flask_app.app_context():
            user = UserProfile.query.filter_by(username=TEST_USERNAME).first()
            user.gather_status   = 'idle'
            user.gather_progress = 0
            _db.session.commit()

            recover_stale_jobs()

            user = UserProfile.query.filter_by(username=TEST_USERNAME).first()
            assert user.gather_status == 'idle'

    def test_done_profile_not_modified(self, flask_app, profile):
        """Profiles at 'done' must not be reset — 'done' is a stable terminal state."""
        with flask_app.app_context():
            user = UserProfile.query.filter_by(username=TEST_USERNAME).first()
            user.gather_status   = 'done'
            user.gather_progress = 100
            _db.session.commit()

            recover_stale_jobs()

            user = UserProfile.query.filter_by(username=TEST_USERNAME).first()
            assert user.gather_status   == 'done'
            assert user.gather_progress == 100
