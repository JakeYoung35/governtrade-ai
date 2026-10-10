"""Request-local tenant contexts; the same context scopes state, vaults and the governor."""
import contextlib
import contextvars
try:
    import fcntl
except ImportError:  # Windows: no fcntl; the thread lock still serializes a single local server.
    fcntl = None
import os
import re
import threading
from pathlib import Path

from governor_service import GovernorService
from agent_chat import AgentChat
from vault import Vault
from workspace import Workspace

CURRENT = contextvars.ContextVar('governtrade_tenant', default=None)
_runtimes = {}
_lock = threading.Lock()


class Tenant:
    def __init__(self, directory, save):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.workspace = Workspace(Vault(directory), save)
        self.governor = GovernorService(self.workspace)
        self.chat = AgentChat(self.workspace, self.governor)

    @contextlib.contextmanager
    def locked(self):
        # Serialize across threads; where fcntl exists (Linux/Render), also across processes sharing this disk.
        with self.lock:
            if fcntl is None:
                yield
                return
            fd = os.open(self.directory / 'workspace.lock', os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)


@contextlib.contextmanager
def activate(root, user_id, save):
    if not re.fullmatch(r'[0-9a-f]{64}', user_id):
        raise ValueError('Invalid verified user identity.')
    directory = Path(root) / 'users' / user_id
    with _lock:
        runtime = _runtimes.get(directory)
        if runtime is None:
            runtime = _runtimes[directory] = Tenant(directory, save)
    token = CURRENT.set(runtime)
    try:
        yield runtime
    finally:
        CURRENT.reset(token)
