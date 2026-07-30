from __future__ import annotations

from scripts import cbm_extension_mcp


def test_local_extension_mcp_registers_only_recorder_tools(monkeypatch):
    registered: list[str] = []

    class FakeFastMCP:
        def __init__(self, *args, **kwargs):
            pass

        def tool(self):
            def decorate(fn):
                registered.append(fn.__name__)
                return fn

            return decorate

    class FakeController:
        def execute(self, operation):
            return {"operation": operation}

    monkeypatch.setattr(cbm_extension_mcp, "_import_fastmcp", lambda: FakeFastMCP)
    cbm_extension_mcp.build_server(FakeController())

    assert registered == [
        "recorder_status",
        "recorder_start",
        "recorder_stop",
        "recorder_export",
        "recorder_clear",
        "recorder_compile",
    ]
