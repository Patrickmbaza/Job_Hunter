"""Persistent Python MCP client for the Codex-configured REA stdio server.

The installed executable and arguments are read from ~/.codex/config.toml. This
module starts one child process using the official Python MCP SDK, discovers
tools at runtime, validates inputs against their live JSON Schemas, and closes
the session/process with the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import tomli
from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import get_settings

# Read-only tools confirmed in the live REA 6.8.0 catalog. The app never
# exposes a generic call_tool endpoint, so mutation-capable tools cannot be
# invoked through the dashboard.
READ_ONLY_TOOL_NAMES = frozenset({
    "list_browser_targets",
    "inspect_web_page",
    "inspect_web_event_listeners",
    "capture_web_screenshot",
    "observe_web_session",
    "discover_webmcp_tools",
})


class ReaBridgeError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class McpServerConfig:
    command: str
    args: tuple[str, ...]
    env: dict[str, str]
    cwd: str | None
    startup_timeout: float
    config_path: str

    @classmethod
    def load_codex_server(cls) -> "McpServerConfig":
        settings = get_settings()
        path = settings.rea_mcp_config_path
        if not path.is_file():
            raise ReaBridgeError("config_missing", f"Codex MCP config was not found at {path}")
        try:
            document = tomli.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomli.TOMLDecodeError):
            raise ReaBridgeError("config_invalid", "Codex MCP config could not be read") from None
        servers = document.get("mcp_servers", {})
        entry = servers.get(settings.rea_mcp_server_name) if isinstance(servers, dict) else None
        if not isinstance(entry, dict) or entry.get("enabled", True) is False:
            raise ReaBridgeError("server_not_configured", "The configured REA MCP server is unavailable")
        command = entry.get("command")
        args = entry.get("args", [])
        env_config = entry.get("env", {})
        cwd = entry.get("cwd")
        if not isinstance(command, str) or not command or not isinstance(args, list) or not all(isinstance(x, str) for x in args):
            raise ReaBridgeError("unsupported_transport", "REA must be configured as a stdio MCP server")
        if not isinstance(env_config, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env_config.items()):
            raise ReaBridgeError("config_invalid", "REA MCP environment configuration is invalid")
        startup_timeout = entry.get("startup_timeout_sec", settings.rea_startup_timeout_seconds)
        if not isinstance(startup_timeout, (int, float)) or not 1 <= startup_timeout <= 120:
            startup_timeout = settings.rea_startup_timeout_seconds
        # Do not pass the app's secrets (APP_PASSWORD_HASH, database settings,
        # etc.) to the REA child. Preserve only the process environment REA and
        # its native providers need, then apply explicit Codex MCP env entries.
        inherited_names = {
            "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TERM",
            "TMPDIR", "TMP", "TEMP", "SYSTEMROOT", "WINDIR", "WSLENV",
            "WSL_INTEROP", "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
            "NODE_OPTIONS",
        }
        child_env = {key: value for key, value in os.environ.items() if key in inherited_names}
        child_env.update(env_config)
        return cls(
            command=command,
            args=tuple(args),
            env=child_env,
            cwd=cwd if isinstance(cwd, str) else None,
            startup_timeout=float(startup_timeout),
            config_path=str(path),
        )


class ReaIntegrationService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._stdio_context: Any = None
        self._stderr_file: Any = None
        self._session_context: Any = None
        self._session: ClientSession | None = None
        self._tools: dict[str, Any] = {}
        self._server_info: dict[str, Any] = {}
        self._connected_at: float | None = None
        self._last_error: dict[str, str] | None = None
        self._last_browser_check: dict[str, Any] | None = None
        self._last_targets: dict[str, str] = {}
        self._last_targets_at: float | None = None

    @property
    def connected(self) -> bool:
        return self._session is not None

    def _remember_error(self, code: str, message: str) -> None:
        # Deliberately avoid returning raw process/MCP output; it can contain
        # target data, command details, or configured environment values.
        self._last_error = {"code": code, "message": message[:240]}

    async def _dispose_locked(self) -> None:
        session_context, stdio_context, stderr_file = self._session_context, self._stdio_context, self._stderr_file
        self._session = None
        self._session_context = None
        self._stdio_context = None
        self._stderr_file = None
        self._tools = {}
        self._server_info = {}
        self._connected_at = None
        self._last_targets = {}
        self._last_targets_at = None
        if session_context is not None:
            try:
                await asyncio.wait_for(session_context.__aexit__(None, None, None), timeout=3)
            except Exception:
                pass
        if stdio_context is not None:
            try:
                await asyncio.wait_for(stdio_context.__aexit__(None, None, None), timeout=5)
            except Exception:
                pass
        if stderr_file is not None:
            try:
                stderr_file.close()
            except Exception:
                pass

    async def _discover_all_tools(self, session: ClientSession) -> dict[str, Any]:
        found: dict[str, Any] = {}
        cursor: str | None = None
        for _ in range(25):
            page = await asyncio.wait_for(session.list_tools(cursor=cursor), timeout=20)
            for tool in page.tools:
                found[tool.name] = tool
            cursor = getattr(page, "nextCursor", None)
            if not cursor:
                return found
        raise ReaBridgeError("catalog_pagination_limit", "REA tool catalog exceeded the safe page limit")

    async def _connect_locked(self) -> None:
        if self._session is not None:
            return
        config = McpServerConfig.load_codex_server()
        last_failure = "REA MCP server did not initialize"
        # REA's MCP guidance permits one startup retry. Unchanged startup
        # failures are not retried a third time.
        for attempt in range(2):
            try:
                params = StdioServerParameters(
                    command=config.command,
                    args=list(config.args),
                    env=config.env,
                    cwd=config.cwd,
                    encoding="utf-8",
                    encoding_error_handler="replace",
                )
                self._stderr_file = open(os.devnull, "w", encoding="utf-8")
                self._stdio_context = stdio_client(params, errlog=self._stderr_file)
                read_stream, write_stream = await asyncio.wait_for(
                    self._stdio_context.__aenter__(), timeout=config.startup_timeout
                )
                self._session_context = ClientSession(read_stream, write_stream)
                self._session = await asyncio.wait_for(
                    self._session_context.__aenter__(), timeout=config.startup_timeout
                )
                initialized = await asyncio.wait_for(
                    self._session.initialize(), timeout=config.startup_timeout
                )
                tools = await self._discover_all_tools(self._session)
                if not tools:
                    raise ReaBridgeError("empty_tool_catalog", "REA connected but returned no tools")
                self._tools = tools
                server_info = getattr(initialized, "serverInfo", None)
                self._server_info = server_info.model_dump(mode="json") if server_info else {}
                self._connected_at = time.time()
                self._last_error = None
                return
            except Exception as exc:
                last_failure = type(exc).__name__
                await self._dispose_locked()
                if attempt == 0:
                    await asyncio.sleep(0.25)
        self._remember_error("startup_failed", f"REA MCP startup failed ({last_failure})")
        raise ReaBridgeError("startup_failed", "REA MCP server could not be started; see the local diagnostics") from None

    async def ensure_connected(self) -> None:
        async with self._lock:
            await self._connect_locked()

    async def _call_readonly_locked(self, name: str, arguments: dict[str, Any], *, may_retry: bool = True) -> Any:
        if name not in READ_ONLY_TOOL_NAMES:
            raise ReaBridgeError("operation_not_allowed", "This REA operation is not enabled in the app")
        for attempt in range(2 if may_retry else 1):
            await self._connect_locked()
            tool = self._tools.get(name)
            if tool is None:
                raise ReaBridgeError("tool_unavailable", f"REA did not advertise the required read-only tool: {name}")
            schema = getattr(tool, "inputSchema", None)
            if not isinstance(schema, dict):
                raise ReaBridgeError("schema_unavailable", f"REA did not advertise an input schema for {name}")
            try:
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(arguments)
            except Exception as exc:
                if exc.__class__.__module__.startswith("jsonschema"):
                    raise ReaBridgeError("invalid_arguments", f"Arguments do not match the current REA schema for {name}") from None
                raise ReaBridgeError("schema_invalid", f"REA advertised an invalid input schema for {name}") from None
            try:
                assert self._session is not None
                result = await asyncio.wait_for(
                    self._session.call_tool(name, arguments),
                    timeout=get_settings().rea_tool_timeout_seconds,
                )
                if getattr(result, "isError", False):
                    return result
                output_schema = getattr(tool, "outputSchema", None)
                structured = getattr(result, "structuredContent", None)
                if structured is not None and isinstance(output_schema, dict):
                    try:
                        Draft202012Validator(output_schema).validate(structured)
                    except Exception:
                        raise ReaBridgeError("invalid_tool_result", f"REA returned a result outside its advertised schema for {name}") from None
                return result
            except asyncio.TimeoutError:
                self._remember_error("tool_timeout", f"REA {name} exceeded the configured deadline")
                await self._dispose_locked()
                if attempt == 0 and may_retry:
                    continue
                raise ReaBridgeError("tool_timeout", f"REA {name} exceeded the configured deadline", retryable=True) from None
            except ReaBridgeError:
                raise
            except Exception as exc:
                self._remember_error("mcp_call_failed", f"REA {name} transport failed ({type(exc).__name__})")
                await self._dispose_locked()
                if attempt == 0 and may_retry:
                    continue
                raise ReaBridgeError("mcp_call_failed", f"REA {name} transport failed", retryable=True) from None
        raise ReaBridgeError("mcp_call_failed", "REA call failed")

    async def call_readonly(self, name: str, arguments: dict[str, Any]) -> Any:
        async with self._lock:
            return await self._call_readonly_locked(name, arguments)

    async def status(self) -> dict[str, Any]:
        try:
            await self.ensure_connected()
            return {
                "state": "connected",
                "transport": "stdio",
                "server": self._server_info,
                "tool_count": len(self._tools),
                "readonly_tools": sorted(self._tools.keys() & READ_ONLY_TOOL_NAMES),
                "connected_since": self._connected_at,
                "last_error": self._last_error,
                "browser": self._last_browser_check or {"state": "not_tested"},
            }
        except ReaBridgeError as exc:
            return {
                "state": "unavailable",
                "transport": "stdio",
                "server": None,
                "tool_count": 0,
                "readonly_tools": [],
                "connected_since": None,
                "last_error": {"code": exc.code, "message": str(exc)},
                "browser": self._last_browser_check or {"state": "not_tested"},
            }

    def public_tools(self) -> list[dict[str, Any]]:
        items = []
        for name in sorted(self._tools.keys() & READ_ONLY_TOOL_NAMES):
            tool = self._tools[name]
            items.append({
                "name": name,
                "description": getattr(tool, "description", None),
                "inputSchema": getattr(tool, "inputSchema", None),
            })
        return items

    async def discover_targets(self, allowed_origins: list[str]) -> Any:
        args: dict[str, Any] = {"cdp_endpoint": get_settings().rea_cdp_endpoint}
        if allowed_origins:
            args["allowed_origins"] = allowed_origins
        result = await self.call_readonly("list_browser_targets", args)
        checked_at = time.time()
        if getattr(result, "isError", False):
            self._last_targets = {}
            self._last_browser_check = {"state": "unavailable", "checked_at": checked_at, "target_count": 0}
            return result
        payload = self._payload_for_tool("list_browser_targets", result)
        normalized = payload.get("normalized_result") if isinstance(payload, dict) else None
        targets = normalized.get("targets") if isinstance(normalized, dict) else None
        self._last_targets = {
            item["target_id"]: item["origin"]
            for item in targets or []
            if isinstance(item, dict) and isinstance(item.get("target_id"), str) and isinstance(item.get("origin"), str)
        }
        self._last_targets_at = checked_at
        self._last_browser_check = {
            "state": "connected" if isinstance(targets, list) else "result_unavailable",
            "checked_at": checked_at,
            "target_count": len(targets) if isinstance(targets, list) else None,
        }
        return result

    async def inspect_page(self, target_id: str, allowed_origins: list[str]) -> Any:
        args: dict[str, Any] = {
            "cdp_endpoint": get_settings().rea_cdp_endpoint,
            "target_id": target_id,
            "include_accessibility_text": True,
        }
        if allowed_origins:
            args["allowed_origins"] = allowed_origins
        return await self.call_readonly("inspect_web_page", args)

    def _payload_for_tool(self, name: str, result: Any) -> dict[str, Any] | None:
        tool = self._tools.get(name)
        schema = getattr(tool, "outputSchema", None) if tool else None
        candidates: list[Any] = []
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict):
            candidates.append(structured)
        for block in getattr(result, "content", []):
            text = getattr(block, "text", None)
            if isinstance(text, str):
                try:
                    parsed = json.loads(text)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(parsed, dict):
                    candidates.append(parsed)
        for candidate in candidates:
            if isinstance(schema, dict):
                try:
                    Draft202012Validator(schema).validate(candidate)
                except Exception:
                    continue
            return candidate
        return None

    def target_was_discovered(self, target_id: str, origin: str) -> bool:
        return (
            self._last_targets_at is not None
            and time.time() - self._last_targets_at <= 300
            and self._last_targets.get(target_id) == origin
        )

    async def close(self) -> None:
        async with self._lock:
            await self._dispose_locked()


def mcp_result_json(result: Any) -> dict[str, Any]:
    """Serialize the MCP protocol result without assuming REA text payload shape."""
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json", by_alias=True, exclude_none=True)
    raise ReaBridgeError("invalid_mcp_result", "REA returned an unsupported MCP result type")


rea_service = ReaIntegrationService()
