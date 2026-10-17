import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple, Any
from uuid import uuid1

import utils


@dataclass
class Session:
    session_id: str
    browser: Any
    created_at: datetime

    def lifetime(self) -> timedelta:
        return datetime.now() - self.created_at


class SessionsStorage:
    """SessionsStorage creates, stores and processes all the browser sessions"""

    def __init__(self):
        self.sessions = {}

    def create(self, session_id: Optional[str] = None, proxy: Optional[dict] = None,
               force_new: Optional[bool] = False) -> Tuple[Session, bool]:
        session_id = session_id or str(uuid1())

        if force_new:
            self.destroy(session_id)

        if self.exists(session_id):
            return self.sessions[session_id], False

        if not utils.browser_semaphore.acquire(timeout=60):
            raise Exception("Timeout waiting for available browser slot.")

        try:
            browser = utils.run_async(utils.start_browser(proxy))
        except Exception:
            utils.browser_semaphore.release()
            raise

        created_at = datetime.now()
        session = Session(session_id, browser, created_at)

        self.sessions[session_id] = session

        return session, True

    def exists(self, session_id: str) -> bool:
        return session_id in self.sessions

    def destroy(self, session_id: str) -> bool:
        if not self.exists(session_id):
            return False

        session = self.sessions.pop(session_id)
        try:
            utils.run_async(utils.stop_browser(session.browser))
        finally:
            utils.browser_semaphore.release()
        return True

    def get(self, session_id: str, ttl: Optional[timedelta] = None) -> Tuple[Session, bool]:
        session, fresh = self.create(session_id)

        if ttl is not None and not fresh and session.lifetime() > ttl:
            logging.debug("session's lifetime has expired, recreating (session_id=%s)", session_id)
            session, fresh = self.create(session_id, force_new=True)

        return session, fresh

    def session_ids(self) -> list[str]:
        return list(self.sessions.keys())
