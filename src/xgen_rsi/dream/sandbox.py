"""Loading and running exploration-policy code safely (R-10, E12, 04 §7).

Policy code written by the policy-development agent is untrusted. Four layers
keep it prefix-only, deterministic and bounded:

1. **Static checks** (:func:`static_check`) on the AST, producing a
   :class:`RejectionReport` (rule, line, message per violation):
   - imports only from :data:`ALLOWED_MODULES` (math, statistics, collections,
     itertools, functools, dataclasses, typing, heapq, bisect,
     ``xgen_rsi.explore.api``/``signals``; ``see.policy.*`` are accepted
     aliases); no relative or star imports;
   - no forbidden builtins (open, eval, exec, compile, __import__, getattr,
     setattr, delattr, vars, dir, globals, locals, input, breakpoint, id, hash,
     type, ...) and no dunder names;
   - no dunder attributes (except ``__init__``), no frame/code/closure
     introspection attributes, no ``_private`` attribute of any object other
     than ``self``/``cls``/``super()``;
   - prefix-only: no ``.best_so_far`` / ``.budget_spent``; no string literal
     (docstrings excepted) equal to a world cell id or node id; no float
     literal (more than three decimals) equal to a recorded world score;
   - timeouts must propagate: no bare ``except``, no ``BaseException`` and
     friends, no ``finally``, no ``__exit__``/``__del__`` definitions, no async;
   - ``str.format`` only on a constant format string without attribute or
     index fields (format strings can walk attributes);
   - no assignment to attributes of imported names.
2. **Restricted runtime**: the module executes with a curated builtins dict
   and a guarded ``__import__`` that hands out *proxies* exposing only a
   module's public, non-module names (so ``dataclasses.sys`` does not exist).
   Every episode executes the compiled module in a fresh namespace, so class
   attributes or globals cannot carry information between episodes; a
   post-episode integrity check fails the episode if a shared class or
   function was mutated.
3. **Object capabilities**: the policy only receives a ``ReplayQuestion``
   facade whose state lives in closures (``dream.replay``).
4. **Wall-clock limit**: :func:`run_limited` runs the episode in a worker
   thread with a deadline enforced by a line tracer installed only for frames
   of the policy's own code object, plus deadline checks in every question
   method. A thread that still does not stop is abandoned (daemon).

Determinism: random, time, os, sys, ... are not importable; ``id``/``hash`` are
unavailable. Iteration order of sets of strings depends on PYTHONHASHSEED
across processes — the question API returns sorted lists, and the built-in
policies sort explicitly.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import importlib
import string
import sys
import threading
import time
import types
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from xgen_rsi.explore.api import LLMDesignedMethod

ALLOWED_MODULES = frozenset({
    "__future__", "math", "statistics", "collections", "collections.abc", "itertools",
    "functools", "dataclasses", "typing", "heapq", "bisect",
    "xgen_rsi.explore.api", "xgen_rsi.explore.signals",
})
MODULE_ALIASES = {
    "see.policy.api": "xgen_rsi.explore.api",
    "see.policy.observation_signal": "xgen_rsi.explore.signals",
}
_PARENT_PACKAGES = frozenset({"xgen_rsi", "xgen_rsi.explore", "see", "see.policy"})

FORBIDDEN_NAMES = frozenset({
    "open", "eval", "exec", "compile", "__import__", "getattr", "setattr", "delattr", "vars",
    "dir", "globals", "locals", "input", "breakpoint", "help", "exit", "quit", "memoryview",
    "id", "hash", "type", "BaseException", "SystemExit", "KeyboardInterrupt", "GeneratorExit",
    "copyright", "credits", "license",
})
INTROSPECTION_ATTRS = frozenset({
    "gi_frame", "gi_code", "gi_yieldfrom", "gi_running", "cr_frame", "cr_code", "cr_await",
    "ag_frame", "ag_code", "ag_await", "f_back", "f_globals", "f_locals", "f_builtins",
    "f_code", "f_trace", "f_lasti", "tb_frame", "tb_next", "tb_lasti", "co_consts", "co_code",
    "co_names", "cell_contents", "func_globals", "func_closure", "im_func", "im_self", "mro",
})
PREFIX_ONLY_ATTRS = frozenset({"best_so_far", "budget_spent"})
FORBIDDEN_METHODS = frozenset({"__exit__", "__aexit__", "__del__"})
ALLOWED_DUNDER_ATTRS = frozenset({"__init__"})
ALLOWED_DUNDER_NAMES = frozenset({"__name__"})

_SAFE_BUILTIN_NAMES = (
    "abs", "all", "any", "ascii", "bin", "bool", "bytes", "callable", "chr", "classmethod",
    "complex", "dict", "divmod", "enumerate", "filter", "float", "format", "frozenset",
    "hasattr", "hex", "int", "isinstance", "issubclass", "iter", "len", "list", "map", "max",
    "min", "next", "object", "oct", "ord", "pow", "property", "range", "repr", "reversed",
    "round", "set", "slice", "sorted", "staticmethod", "str", "sum", "super", "tuple", "zip",
    "NotImplemented", "Ellipsis", "Exception", "ArithmeticError", "AssertionError",
    "AttributeError", "IndexError", "KeyError", "LookupError", "NotImplementedError",
    "OverflowError", "RuntimeError", "StopIteration", "TypeError", "ValueError",
    "ZeroDivisionError", "__build_class__",
)

POLICY_MODULE_NAME = "xgen_rsi.dream.policy_module"
"""Placeholder module name for policy classes (``dataclasses`` resolves string
annotations through ``sys.modules[cls.__module__]``); the module itself is empty."""
sys.modules.setdefault(POLICY_MODULE_NAME, types.ModuleType(POLICY_MODULE_NAME))


class PolicyTimeout(BaseException):
    """The policy exceeded its wall-clock limit (a BaseException: policy code cannot catch it)."""


class PolicyIntegrityError(RuntimeError):
    """The policy mutated a shared (imported) class or function during an episode."""


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str
    line: int = 0

    def to_json(self) -> dict[str, Any]:
        return {"rule": self.rule, "message": self.message, "line": self.line}


@dataclass(frozen=True)
class RejectionReport:
    """Outcome of the static checks (and of loading)."""

    violations: tuple[Violation, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def rules(self) -> set[str]:
        return {v.rule for v in self.violations}

    def summary(self) -> str:
        if self.ok:
            return "ok"
        return "\n".join(f"line {v.line}: [{v.rule}] {v.message}" for v in self.violations)

    def to_json(self) -> dict[str, Any]:
        return {"ok": self.ok, "violations": [v.to_json() for v in self.violations]}


class PolicyRejected(Exception):
    """Policy source failed the static checks, compilation or loading."""

    def __init__(self, report: RejectionReport) -> None:
        super().__init__(report.summary())
        self.report = report


# ------------------------------------------------------------ static checks --


def _decimals(x: float) -> int:
    text = repr(float(x))
    if "e" in text or "." not in text:
        return 0
    return len(text.split(".")[1].rstrip("0"))


class _Checker(ast.NodeVisitor):
    def __init__(self, cell_ids: frozenset[str], scores: tuple[float, ...]) -> None:
        self.cell_ids = cell_ids
        self.scores = scores
        self.violations: list[Violation] = []
        self.imported: set[str] = set()
        self.docstrings: set[int] = set()

    def flag(self, node: ast.AST, rule: str, message: str) -> None:
        self.violations.append(Violation(rule, message, getattr(node, "lineno", 0)))

    # imports
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = MODULE_ALIASES.get(alias.name, alias.name)
            if name not in ALLOWED_MODULES:
                self.flag(node, "import", f"import of {alias.name!r} is not allowed")
            self.imported.add((alias.asname or alias.name).split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            self.flag(node, "import", "relative imports are not allowed")
            return
        mod = MODULE_ALIASES.get(node.module or "", node.module or "")
        for alias in node.names:
            if alias.name == "*":
                self.flag(node, "import", "star imports are not allowed")
                continue
            sub = MODULE_ALIASES.get(f"{node.module}.{alias.name}", f"{mod}.{alias.name}")
            if mod not in ALLOWED_MODULES and sub not in ALLOWED_MODULES:
                self.flag(node, "import", f"import from {node.module!r} is not allowed")
            self.imported.add(alias.asname or alias.name)

    # names and attributes
    def visit_Name(self, node: ast.Name) -> None:
        nid = node.id
        if nid in FORBIDDEN_NAMES:
            self.flag(node, "forbidden-name", f"use of {nid!r} is not allowed")
        elif nid.startswith("__") and nid.endswith("__") and nid not in ALLOWED_DUNDER_NAMES:
            self.flag(node, "forbidden-name", f"dunder name {nid!r} is not allowed")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        if attr.startswith("__") and attr.endswith("__"):
            if attr not in ALLOWED_DUNDER_ATTRS:
                self.flag(node, "dunder-attribute", f"attribute {attr!r} is not allowed")
        elif attr.startswith("_") and not _is_self_like(node.value):
            self.flag(node, "private-attribute",
                      f"private attribute {attr!r} of another object is not allowed")
        if attr in INTROSPECTION_ATTRS:
            self.flag(node, "introspection", f"introspection attribute {attr!r} is not allowed")
        if attr in PREFIX_ONLY_ATTRS:
            self.flag(node, "prefix-only",
                      f"question.{attr} is bookkeeping only; derive decisions from observed()")
        if attr in ("format", "format_map"):
            self._check_format(node)
        self.generic_visit(node)

    def _check_format(self, node: ast.Attribute) -> None:
        base = node.value
        if not (isinstance(base, ast.Constant) and isinstance(base.value, str)):
            self.flag(node, "format-string", "str.format is allowed only on a constant string")
            return
        problem = _format_fields_problem(base.value)
        if problem:
            self.flag(node, "format-string", problem)

    def visit_Constant(self, node: ast.Constant) -> None:
        if id(node) in self.docstrings:
            return
        v = node.value
        if isinstance(v, str) and v in self.cell_ids:
            self.flag(node, "hardcoded-cell-id", f"string literal {v!r} is a world cell/node id")
        elif isinstance(v, float) and _decimals(v) > 3:
            if any(abs(v - s) <= 1e-12 for s in self.scores):
                self.flag(node, "hardcoded-score", f"float literal {v!r} equals a recorded score")

    # control flow that could swallow timeouts
    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self.flag(node, "catch-all", "bare except is not allowed (use except Exception)")
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        if node.finalbody:
            self.flag(node, "finally", "finally blocks are not allowed in policy code")
        self.generic_visit(node)

    visit_TryStar = visit_Try

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name in FORBIDDEN_METHODS:
            self.flag(node, "forbidden-method", f"defining {node.name!r} is not allowed")
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.flag(node, "async", "async code is not allowed in policy code")

    def visit_Await(self, node: ast.Await) -> None:
        self.flag(node, "async", "await is not allowed in policy code")

    # mutation of imported names
    def _check_target(self, target: ast.AST) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            for t in target.elts:
                self._check_target(t)
            return
        if isinstance(target, ast.Attribute):
            root = target.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in self.imported:
                self.flag(target, "mutate-import",
                          f"assignment to an attribute of imported {root.id!r} is not allowed")

    def visit_Assign(self, node: ast.Assign) -> None:
        for t in node.targets:
            self._check_target(t)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._check_target(node.target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._check_target(node.target)
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        for t in node.targets:
            self._check_target(t)
        self.generic_visit(node)


def _format_fields_problem(fmt: str, depth: int = 0) -> str | None:
    """Reject format fields that walk attributes or indexes (``{0.x}``, ``{0[k]}``)."""
    if depth > 2:
        return "format specs nested too deeply"
    try:
        parsed = list(string.Formatter().parse(fmt))
    except ValueError as exc:
        return f"bad format string: {exc}"
    for _lit, fname, spec, _conv in parsed:
        if fname and ("." in fname or "[" in fname):
            return "format fields with attribute or index access are not allowed"
        if spec and "{" in spec:
            nested = _format_fields_problem(spec, depth + 1)
            if nested:
                return nested
    return None


def _is_self_like(node: ast.AST) -> bool:
    if isinstance(node, ast.Name) and node.id in ("self", "cls"):
        return True
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "super")


def static_check(source: str, *, cell_ids: Iterable[str] = (),
                 score_values: Iterable[float] = ()) -> RejectionReport:
    """Run every static rule on ``source``; never raises (syntax errors become violations)."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return RejectionReport((Violation("syntax", str(exc), exc.lineno or 0),))
    checker = _Checker(frozenset(cell_ids), tuple(float(s) for s in score_values))
    # imports first so mutate-import knows every imported name
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                checker.imported.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                checker.imported.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                checker.docstrings.add(id(body[0].value))
    checker.visit(tree)
    seen: set[tuple[str, str, int]] = set()
    unique = []
    for v in checker.violations:
        key = (v.rule, v.message, v.line)
        if key not in seen:
            seen.add(key)
            unique.append(v)
    return RejectionReport(tuple(unique))


def check_structure(source: str, *, class_name: str = "OptimalPolicy",
                    require: tuple[str, ...] = ("solve", "plan_grid")) -> RejectionReport:
    """Develop-agent deliverable shape: ``NAME = "<class_name>"`` and
    ``class <class_name>(...)`` defining every method in ``require`` in its body."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return RejectionReport((Violation("syntax", str(exc), exc.lineno or 0),))
    out: list[Violation] = []
    name_ok = any(
        isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "NAME"
                                          for t in n.targets)
        and isinstance(n.value, ast.Constant) and n.value.value == class_name
        for n in tree.body)
    if not name_ok:
        out.append(Violation("structure", f'module must set NAME = "{class_name}"'))
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name), None)
    if cls is None:
        out.append(Violation("structure", f"module must define class {class_name}"))
    else:
        defined = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
        for meth in require:
            if meth not in defined:
                out.append(Violation("structure", f"{class_name} must override {meth}()",
                                     cls.lineno))
    return RejectionReport(tuple(out))


# --------------------------------------------------------- runtime namespace --


def _public_names(mod: types.ModuleType) -> list[str]:
    names = getattr(mod, "__all__", None)
    if names is None:
        names = [n for n in dir(mod) if not n.startswith("_")]
    return [n for n in names if hasattr(mod, n) and not isinstance(getattr(mod, n), types.ModuleType)]


def _module_proxy(name: str) -> types.SimpleNamespace:
    mod = importlib.import_module(name)
    ns = types.SimpleNamespace(**{n: getattr(mod, n) for n in _public_names(mod)})
    ns.__name__ = name
    return ns


def _guarded_import(name: str, globals_: Any = None, locals_: Any = None,
                    fromlist: Any = (), level: int = 0) -> Any:
    if level:
        raise ImportError("relative imports are not allowed in policy code")
    real = MODULE_ALIASES.get(name, name)
    if not fromlist:
        if real not in ALLOWED_MODULES:
            raise ImportError(f"import of {name!r} is not allowed in policy code")
        if real == "__future__":
            return _module_proxy("__future__")
        parts = name.split(".")
        leaf: Any = _module_proxy(real)
        for i in range(len(parts) - 1, 0, -1):
            parent_name = ".".join(parts[:i])
            parent_real = MODULE_ALIASES.get(parent_name, parent_name)
            parent = (_module_proxy(parent_real) if parent_real in ALLOWED_MODULES
                      else types.SimpleNamespace(__name__=parent_name))
            setattr(parent, parts[i], leaf)
            leaf = parent
        return leaf
    if real in ALLOWED_MODULES:
        proxy = _module_proxy(real)
    elif real in _PARENT_PACKAGES:
        proxy = types.SimpleNamespace(__name__=name)
    else:
        raise ImportError(f"import from {name!r} is not allowed in policy code")
    for item in fromlist:
        sub = MODULE_ALIASES.get(f"{name}.{item}", f"{real}.{item}")
        if sub in ALLOWED_MODULES:
            setattr(proxy, item, _module_proxy(sub))
        elif not hasattr(proxy, item):
            raise ImportError(f"cannot import {item!r} from {name!r} in policy code")
    return proxy


def _noop_print(*_args: Any, **_kwargs: Any) -> None:
    return None


def _safe_builtins() -> dict[str, Any]:
    out = {n: getattr(builtins, n) for n in _SAFE_BUILTIN_NAMES}
    out["__import__"] = _guarded_import
    out["print"] = _noop_print
    return out


def _shared_objects(namespace: dict[str, Any]) -> list[Any]:
    """Classes and functions reachable from imported proxies (the cross-episode surface)."""
    objs: list[Any] = [LLMDesignedMethod]
    for v in namespace.values():
        cands = list(vars(v).values()) if isinstance(v, types.SimpleNamespace) else [v]
        for c in cands:
            if isinstance(c, (type, types.FunctionType)) and getattr(c, "__module__", "") != POLICY_MODULE_NAME:
                objs.append(c)
    return objs


def _snapshot(objs: list[Any]) -> dict[int, tuple[Any, ...]]:
    snap = {}
    for o in objs:
        try:
            d = vars(o)
        except TypeError:
            continue
        snap[id(o)] = tuple(sorted((k, id(v)) for k, v in d.items()))
    return snap


# ------------------------------------------------------------- run limits --


def run_limited(fn: Callable[[], Any], *, timeout: float | None,
                trace_filename: str | None = None, grace: float = 2.0) -> Any:
    """Run ``fn()`` with a wall-clock limit; return its value or re-raise its exception.

    ``timeout=None`` runs inline without limits. Otherwise ``fn`` runs in a
    daemon worker thread; a line tracer installed for frames whose code file
    is ``trace_filename`` raises :class:`PolicyTimeout` once the deadline has
    passed (the tracer is not installed for other frames, so host code runs at
    full speed). If the thread does not finish within ``timeout + grace`` it is
    abandoned and :class:`PolicyTimeout` is raised.
    """
    if timeout is None:
        return fn()
    deadline = time.monotonic() + float(timeout)
    box: dict[str, Any] = {}

    def local(frame: Any, event: str, arg: Any) -> Any:
        if event == "line" and time.monotonic() > deadline:
            raise PolicyTimeout(f"policy exceeded the wall-clock limit of {timeout}s")
        return local

    def global_tracer(frame: Any, event: str, arg: Any) -> Any:
        if frame.f_code.co_filename == trace_filename:
            if time.monotonic() > deadline:
                raise PolicyTimeout(f"policy exceeded the wall-clock limit of {timeout}s")
            return local
        return None

    def worker() -> None:
        if trace_filename is not None:
            sys.settrace(global_tracer)
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — re-raised in the caller thread
            box["error"] = exc
        finally:
            sys.settrace(None)

    t = threading.Thread(target=worker, daemon=True, name="dream-policy")
    t.start()
    t.join(float(timeout) + grace)
    if t.is_alive():
        raise PolicyTimeout(f"policy did not stop within {timeout}s (+{grace}s grace); abandoned")
    if "error" in box:
        raise box["error"]
    return box.get("value")


#: 이미 격리된 자식 프로세스 안인가(중첩 fork 를 막는다).
_IN_CHILD = False
#: 자식 프로세스의 주소 공간 상한(바이트).
CHILD_MEMORY_LIMIT = 2 * 1024 ** 3


def can_isolate() -> bool:
    import multiprocessing as mp

    return not _IN_CHILD and "fork" in mp.get_all_start_methods()


def run_isolated(fn: Callable[[], Any], *, timeout: float | None) -> Any:
    """``fn()`` in a forked child with CPU-time and address-space limits; return its (picklable) value.

    The parent kills the child at ``timeout + 5`` s. A long C call (which the line tracer of
    :func:`run_limited` cannot interrupt) or a memory bomb ends as :class:`PolicyTimeout` /
    an exception instead of stalling or killing the evaluator. Exceptions raised by ``fn`` are
    re-raised in the parent (as ``RuntimeError`` when they cannot be pickled).
    """
    import math
    import multiprocessing as mp
    import pickle

    ctx = mp.get_context("fork")
    recv, send = ctx.Pipe(duplex=False)
    limit = float(timeout if timeout is not None else 10.0)

    def child() -> None:
        global _IN_CHILD
        _IN_CHILD = True
        try:
            import resource

            cpu = int(math.ceil(limit)) + 1
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
            resource.setrlimit(resource.RLIMIT_AS, (CHILD_MEMORY_LIMIT, CHILD_MEMORY_LIMIT))
        except (ImportError, ValueError, OSError):
            pass
        try:
            payload: Any = ("ok", fn())
        except BaseException as exc:  # noqa: BLE001 — carried to the parent
            payload = ("err", exc)
        try:
            data = pickle.dumps(payload)
        except Exception:  # noqa: BLE001 — unpicklable value or exception
            data = pickle.dumps(("err", RuntimeError(f"{type(payload[1]).__name__}: {payload[1]}"[:500])))
        send.send_bytes(data)

    proc = ctx.Process(target=child, daemon=True, name="dream-policy")
    proc.start()
    send.close()
    payload: Any = None
    if recv.poll(limit + 5.0):
        try:
            payload = pickle.loads(recv.recv_bytes())
        except (EOFError, OSError, pickle.UnpicklingError):
            payload = None
    if proc.is_alive():
        proc.kill()
    proc.join(5)
    recv.close()
    if payload is None:
        if proc.exitcode in (None, -9):
            raise PolicyTimeout(f"policy process exceeded {limit + 5.0:.0f}s and was killed")
        raise PolicyTimeout(f"policy process died (exit code {proc.exitcode}: CPU or memory limit)")
    kind, value = payload
    if kind == "err":
        raise value
    return value


def deadline_after(timeout: float | None) -> float | None:
    """Absolute monotonic deadline for question-side checks (None = unlimited)."""
    return None if timeout is None else time.monotonic() + float(timeout)


def check_deadline(deadline: float | None) -> None:
    """Raise :class:`PolicyTimeout` when ``deadline`` (monotonic) has passed."""
    if deadline is not None and time.monotonic() > deadline:
        raise PolicyTimeout("policy exceeded its wall-clock limit")


# ---------------------------------------------------------------- loading --


@dataclass(frozen=True)
class LoadedPolicy:
    """A statically checked, compiled policy module.

    Calling it (``loaded(config)``) executes the module in a fresh restricted
    namespace and instantiates the policy class with ``config`` — one fresh
    namespace per episode. Use :meth:`run` to do that plus work under the
    wall-clock limit and the shared-state integrity check.
    """

    source: str
    sha256: str
    filename: str
    class_name: str
    timeout: float | None
    code: types.CodeType = field(repr=False)
    report: RejectionReport = field(default_factory=RejectionReport)

    def _exec_module(self) -> tuple[type, dict[str, Any]]:
        namespace: dict[str, Any] = {"__builtins__": _safe_builtins(),
                                     "__name__": POLICY_MODULE_NAME}
        exec(self.code, namespace)  # noqa: S102 — restricted namespace, statically checked
        cls = namespace.get(str(namespace.get("NAME") or self.class_name))
        if cls is None:
            cls = namespace.get(self.class_name)
        if not (isinstance(cls, type) and issubclass(cls, LLMDesignedMethod)):
            raise PolicyRejected(RejectionReport((Violation(
                "structure", f"{self.class_name} must be a subclass of LLMDesignedMethod"),)))
        return cls, namespace

    def fresh_class(self) -> type:
        """Execute the module in a fresh restricted namespace and return the policy class."""
        return self._exec_module()[0]

    def __call__(self, config: dict[str, Any] | None = None) -> Any:
        return self.fresh_class()(config or {})

    def run(self, body: Callable[[Any], Any], config: dict[str, Any] | None = None, *,
            timeout: float | None | str = "default", isolate: bool = True) -> Any:
        """``body(instance)`` under the limits; a fresh namespace for this call.

        With ``isolate`` (default) and a time limit, the call runs in a forked resource-limited child
        and only ``body``'s **return value** comes back — pass ``isolate=False`` when ``body`` must leave
        state in this process (the replay engine isolates the whole episode one level up instead).
        """
        limit = self.timeout if timeout == "default" else timeout

        def task() -> Any:
            cls, ns = self._exec_module()
            shared = _shared_objects(ns)
            before = _snapshot(shared)
            try:
                return body(cls(config or {}))
            finally:
                if _snapshot(shared) != before:
                    raise PolicyIntegrityError("policy mutated a shared class or function")

        if isolate and limit is not None and can_isolate():
            return run_isolated(lambda: run_limited(task, timeout=limit, trace_filename=self.filename),
                                timeout=limit)
        return run_limited(task, timeout=limit, trace_filename=self.filename)


def load_policy(source: str, *, class_name: str = "OptimalPolicy", timeout: float | None = 10.0,
                cell_ids: Iterable[str] = (), score_values: Iterable[float] = (),
                require: tuple[str, ...] = ("solve",)) -> LoadedPolicy:
    """Check, compile and trial-load policy source; raise :class:`PolicyRejected` on failure.

    ``require`` lists methods the policy class must override (the
    development agent passes ``("solve", "plan_grid")``).
    """
    report = static_check(source, cell_ids=cell_ids, score_values=score_values)
    if not report.ok:
        raise PolicyRejected(report)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    filename = f"<policy:{digest[:16]}>"
    try:
        code = compile(source, filename, "exec", dont_inherit=True)
    except SyntaxError as exc:
        raise PolicyRejected(RejectionReport((Violation("syntax", str(exc), exc.lineno or 0),)))
    loaded = LoadedPolicy(source=source, sha256=digest, filename=filename, class_name=class_name,
                          timeout=timeout, code=code, report=report)
    try:
        cls = run_limited(loaded.fresh_class, timeout=timeout, trace_filename=filename)
    except PolicyRejected:
        raise
    except (Exception, PolicyTimeout) as exc:
        raise PolicyRejected(RejectionReport((Violation(
            "load", f"module failed to load: {type(exc).__name__}: {exc}"),))) from None
    missing = [m for m in require
               if getattr(cls, m, None) is getattr(LLMDesignedMethod, m, None)]
    if missing:
        raise PolicyRejected(RejectionReport(tuple(
            Violation("structure", f"{class_name} must override {m}()") for m in missing)))
    return loaded


__all__ = [
    "ALLOWED_MODULES", "FORBIDDEN_NAMES", "MODULE_ALIASES", "LoadedPolicy", "PolicyIntegrityError",
    "PolicyRejected", "PolicyTimeout", "RejectionReport", "Violation", "check_deadline",
    "check_structure", "can_isolate", "deadline_after", "load_policy", "run_isolated", "run_limited", "static_check",
]
