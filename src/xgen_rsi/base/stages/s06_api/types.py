"""Back-compat re-export shim — canonical types live in llm_client.types.

Existing code that imports from ``xgen_rsi.base.stages.s06_api.types``
(tests, vendored providers, external stages) keeps working unchanged.
New code should import from :mod:`xgen_rsi.base.llm_client.types`.

This shim is deleted in PR-4 along with the ``s06_api/artifact/*``
provider directories.
"""

from __future__ import annotations

from xgen_rsi.base.llm_client.types import APIRequest, APIResponse, ContentBlock

__all__ = ["APIRequest", "APIResponse", "ContentBlock"]
