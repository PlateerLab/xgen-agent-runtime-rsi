"""MCP (Model Context Protocol) integration."""

from xgen_rsi.base.tools.mcp.adapter import MCPToolAdapter
from xgen_rsi.base.tools.mcp.credentials import (
    CredentialStore,
    FileCredentialStore,
    MemoryCredentialStore,
    mcp_credential_key,
)
from xgen_rsi.base.tools.mcp.errors import MCPConnectionError
from xgen_rsi.base.tools.mcp.manager import MCPManager, MCPServerConfig
from xgen_rsi.base.tools.mcp.oauth import (
    OAuthAuthConfig,
    OAuthError,
    OAuthFlow,
    OAuthToken,
    build_authorize_url,
    find_free_port,
)
from xgen_rsi.base.tools.mcp.state import (
    RECONNECTABLE_STATES,
    MCPConnectionState,
)
from xgen_rsi.base.tools.mcp.uri import (
    MCP_URI_SCHEME,
    MCPURIError,
    build_mcp_uri,
    is_mcp_uri,
    parse_mcp_uri,
)

__all__ = [
    "CredentialStore",
    "FileCredentialStore",
    "MCPConnectionError",
    "MCPConnectionState",
    "MCPManager",
    "MCPServerConfig",
    "MCPToolAdapter",
    "MCPURIError",
    "MCP_URI_SCHEME",
    "MemoryCredentialStore",
    "OAuthAuthConfig",
    "OAuthError",
    "OAuthFlow",
    "OAuthToken",
    "RECONNECTABLE_STATES",
    "build_authorize_url",
    "build_mcp_uri",
    "find_free_port",
    "is_mcp_uri",
    "mcp_credential_key",
    "parse_mcp_uri",
]
