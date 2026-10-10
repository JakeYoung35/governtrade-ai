"""Request-local tenant contexts; the same context scopes state, vaults and the governor."""
import collections
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
# Loaded tenants, least recently used first. Idle ones beyond the limit are dropped; their state lives on disk.
MAX_TENANTS = int(os.environ.get('GOVERNTRADE_MAX_LOADED_WORKSPACES', '256'))
_runtimes = collections.OrderedDict()
_lock = threading.Lock()


class Tenant:
    def __init__(self, directory, save):
        """save(state, directory) writes a workspace file. The tenant binds its own directory, so a save made outside
        an activate() block (a background thread, a late callback) still lands in this user's folder."""
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self._held = threading.local()  # this thread's nesting depth, so the file lock is taken once
        self.active = 0  # activate() blocks currently using this tenant; guarded by the module _lock
        self.workspace = Workspace(Vault(directory), lambda state: save(state, self.directory))
        self.governor = GovernorService(self.workspace)
        self.chat = AgentChat(self.workspace, self.governor)

    @contextlib.contextmanager
    def locked(self):
        # Serialize across threads; where fcntl exists (Linux/Render, macOS), also across processes sharing this disk.
        # Re-entrant like the RLock: flock belongs to an open file, so only the outermost entry opens and locks it.
        with self.lock:
            depth = getattr(self._held, 'depth', 0)
            fd = None
            if depth == 0 and fcntl is not None:
                fd = os.open(self.directory / 'workspace.lock', os.O_CREAT | os.O_RDWR, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
            self._held.depth = depth + 1
            try:
                yield
            finally:
                self._held.depth = depth
                if fd is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)

    def idle(self):
        """Safe to drop from memory: no request or governor pass is using it and no chat turn is in progress."""
        return self.active == 0 and not self.chat._busy


@contextlib.contextmanager
def activate(root, user_id, save):
    if not re.fullmatch(r'[0-9a-f]{64}', user_id):
        raise ValueError('Invalid verified user identity.')
    directory = Path(root) / 'users' / user_id
    with _lock:
        runtime = _runtimes.get(directory)
        if runtime is None:
            runtime = _runtimes[directory] = Tenant(directory, save)
        _runtimes.move_to_end(directory)
        runtime.active += 1
        for key in [k for k, t in _runtimes.items() if t.idle()][:max(0, len(_runtimes) - MAX_TENANTS)]:
            del _runtimes[key]
    token = CURRENT.set(runtime)
    try:
        yield runtime
    finally:
        CURRENT.reset(token)
        with _lock:
            runtime.active -= 1
