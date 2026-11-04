# REA integration

The backend launches the installed REA MCP server from the configured Codex `mcp_servers.rea` entry using stdio and the official Python MCP SDK. Tool catalog and argument schemas are discovered dynamically and validated. The bridge exposes only reviewed passive page/DOM/accessibility inspection tools; it has no generic tool-call API.

Allowed page origins come from `REA_ALLOWED_ORIGINS`; CDP must be loopback HTTP on an explicit port. Phase 1 verified the separate backend process could connect to REA and inspect the configured example page. MCP and browser status are shown separately.

Current API integration is read-only: no navigation, clicks, typing, uploads, form submission or browser-session creation. Use public APIs/feeds where available. Do not bypass CAPTCHA/MFA, login controls, access restrictions, rate limits or website terms. LinkedIn and Indeed do not have a general public adapter in this version.

To verify, start backend in normal WSL and use the **REA automation** screen to discover allowed targets then inspect a selected target. A successful MCP handshake does not prove CDP reachability. Do not expose CDP publicly. Config defaults to `~/.codex/config.toml`; optional overrides are `REA_MCP_CONFIG_PATH` and `REA_MCP_SERVER_NAME`.
