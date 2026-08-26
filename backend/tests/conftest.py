"""
Starts a fresh mock_emby_server instance on a free local port for any test that
requests the `mock_emby` fixture, and points EmbyClient() at it via `emby_env`.

backend/*.py modules import each other flatly (e.g. `from config import ...`),
which assumes backend/ itself is on sys.path -- true when the real app runs
(uvicorn is launched from inside backend/), but not automatically true for
pytest, so it's added explicitly below.
"""

import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mock_emby_server import create_app  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MockEmbyServer:
    def __init__(self):
        self.app = create_app()
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        for _ in range(500):
            if self._server.started:
                return
            time.sleep(0.01)
        raise RuntimeError("mock Emby server didn't start in time")

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)

    @property
    def state(self) -> dict:
        return self.app.state.mock


@pytest.fixture
def mock_emby():
    server = MockEmbyServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def emby_env(monkeypatch, mock_emby):
    """Points every EmbyClient() construction at the mock server, and speeds up
    the deliberate settle/poll delays emby_sync.py and emby_client.py use
    against a real Emby server (irrelevant against an instant mock)."""
    import emby_client
    import emby_sync

    monkeypatch.setattr(emby_client, "get_emby_config", lambda: {"url": mock_emby.url, "api_key": "test-key"})
    monkeypatch.setattr(emby_client, "_GUIDE_REFRESH_POLL_DELAY_S", 0.01)
    monkeypatch.setattr(emby_sync, "_SETTLE_DELAY_S", 0)
    monkeypatch.setattr(emby_sync, "_INDEX_POLL_DELAY_S", 0)
    return mock_emby
