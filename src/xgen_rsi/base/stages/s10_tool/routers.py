"""Tool routers — backward-compatible re-exports."""

from xgen_rsi.base.stages.s10_tool.interface import ToolRouter
from xgen_rsi.base.stages.s10_tool.artifact.default.routers import RegistryRouter

__all__ = ["ToolRouter", "RegistryRouter"]
