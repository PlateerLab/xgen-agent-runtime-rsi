"""Built-in exploration policies.

- ``parallel_refine`` — π_1, the paper's initial manual policy (the floor).
- ``portfolio`` — a stronger hand-written baseline following the Appendix B
  rules; candidate material for π^0 of the first Dream cycle.

Both modules are self-contained policy *sources* (they import only the
sandbox allow-list), so a Dream cycle can load them through
``dream.sandbox`` exactly like developed policies. Use
:func:`builtin_policy_source` to get the text.
"""

from __future__ import annotations

from pathlib import Path

from .parallel_refine import ParallelRefinePolicy
from .portfolio import PortfolioPolicy

BUILTIN_POLICIES = ("parallel_refine", "portfolio")


def builtin_policy_source(name: str) -> str:
    """Source text of a built-in policy module (``parallel_refine`` or ``portfolio``)."""
    if name not in BUILTIN_POLICIES:
        raise KeyError(f"unknown built-in policy {name!r}; known: {BUILTIN_POLICIES}")
    return (Path(__file__).parent / f"{name}.py").read_text(encoding="utf-8")


__all__ = ["BUILTIN_POLICIES", "ParallelRefinePolicy", "PortfolioPolicy", "builtin_policy_source"]
