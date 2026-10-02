"""Entry point: python -m app.mt5.mcp_server  (stdio MCP bridge to MT5)."""
from app.mt5.mt5_bridge import serve, handle_call, TOOLS_SPEC  # noqa: F401

if __name__ == "__main__":
    serve()
