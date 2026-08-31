import asyncio
import sys

from property_agent import main
from property_agent.main import configure_windows_event_loop


def test_windows_entrypoint_installs_selector_policy(monkeypatch):
    installed = []
    sentinel = object()

    monkeypatch.setattr(asyncio, "WindowsSelectorEventLoopPolicy", lambda: sentinel)
    monkeypatch.setattr(asyncio, "set_event_loop_policy", installed.append)

    configure_windows_event_loop("win32")

    assert installed == [sentinel]


def test_non_windows_entrypoint_keeps_existing_policy(monkeypatch):
    installed = []
    monkeypatch.setattr(asyncio, "set_event_loop_policy", installed.append)

    configure_windows_event_loop("linux")

    assert installed == []


def test_windows_server_uses_selector_runner(monkeypatch):
    calls = []

    class FakeServer:
        async def serve(self):
            calls.append("served")

    class FakeRunner:
        def __init__(self, *, loop_factory):
            calls.append(loop_factory)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def run(self, coroutine):
            coroutine.close()
            calls.append("ran")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(asyncio, "Runner", FakeRunner)
    monkeypatch.setattr(main, "configure_windows_event_loop", lambda: None)
    monkeypatch.setattr("uvicorn.Config", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("uvicorn.Server", lambda _config: FakeServer())

    main.run_server()

    assert calls == [asyncio.SelectorEventLoop, "ran"]
