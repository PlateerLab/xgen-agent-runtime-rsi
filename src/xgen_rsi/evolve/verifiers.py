"""결정적 검증기 — r(x, τ) ∈ [0, 1] 과 가중치, 유효 출력·미제출 판정.

검증기는 **하네스 밖**(커널/도메인 소유)이다. 하네스는 실행 중 검증기를 볼 수 없다(채점기 게이밍 차단,
설계 34 문서 §1). 한 과제의 검증은 검사(check) 목록이고 보상 = 통과 검사 수 / 전체, 가중치 = 전체 검사 수
— RRSI workspace 인스턴스(Harvey LAB criteria 가중)와 같은 규칙이라 Ŝ 가 "전체 검사 중 통과 비율"이 된다.

검사 종류(``kind``):

* ``file_exists`` {path}
* ``file_contains`` {path, text | regex}
* ``file_not_contains`` {path, text | regex}
* ``file_json`` {path, equals? | keys? | length? | lengths?: {"a.b": n} | path_equals?: {"a.b": v}}
* ``file_csv`` {path, header?, rows?, unique?, allowed?: {col: [...]}, cells?: [{row, col, value}]}
* ``file_unchanged`` {path, sha256}
* ``answer_contains`` {text | regex}
* ``answer_not_contains`` {text | regex}
* ``answer_json`` {equals? | keys? | path_equals?}
* ``answer_number`` {value, tol}
* ``answer_criteria`` {criterion, reference?} — 판정 모델이 최종 답이 기준을 만족하는지 본다(RRSI 논문 workspace 인스턴스의
  criteria 채점과 같은 방식). 판정 모델(``judge``)은 하네스 밖이고, 하네스는 실행 중 기준을 보지 못한다. 판정 모델이 없으면
  실패로 기록한다. 에이전트의 사용 기록에서 만든 과제(피드백·기대 답, :mod:`xgen_rsi.usage`)가 이 검사를 쓴다.

``format: true`` 인 검사는 유효 출력(valid_output) 판정에 쓴다. ``required: true`` 인 산출물 검사가 모두 실패하고
최종 글도 비면 미제출(no_submission)이다.

Portions adapted from google-research/rrsi (commit be50316, ``rrsi/llm.py``: the JSON extraction
helper used by ``_parse_json_text``), Copyright 2026 The rrsi Authors / Google LLC, Apache License
2.0; modified by PlateerLab.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class CheckResult:
    name: str
    kind: str
    passed: bool
    detail: str = ""
    format: bool = False


@dataclass(frozen=True)
class VerifierResult:
    reward: float
    weight: float
    valid_output: Optional[bool]
    no_submission: bool
    checks: tuple = ()
    fail_class: str = "ok"

    def to_json(self) -> Dict[str, Any]:
        return {
            "reward": self.reward,
            "weight": self.weight,
            "valid_output": self.valid_output,
            "no_submission": self.no_submission,
            "fail_class": self.fail_class,
            "checks": [c.__dict__ for c in self.checks],
        }


def _read(workspace: str, rel: str) -> Optional[str]:
    path = os.path.realpath(os.path.join(workspace, rel))
    root = os.path.realpath(workspace)
    if not (path == root or path.startswith(root + os.sep)):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _match(haystack: str, check: Mapping[str, Any]) -> bool:
    if "regex" in check:
        return re.search(str(check["regex"]), haystack, re.S | re.M) is not None
    return str(check.get("text", "")) in haystack


def _dig(obj: Any, dotted: str) -> Any:
    node = obj
    for part in dotted.split("."):
        if isinstance(node, list):
            node = node[int(part)]
        elif isinstance(node, dict):
            node = node[part]
        else:
            raise KeyError(dotted)
    return node


def _json_checks(value: Any, check: Mapping[str, Any]) -> tuple:
    if "equals" in check and value != check["equals"]:
        return False, "value differs"
    for key in check.get("keys") or ():
        if not isinstance(value, dict) or key not in value:
            return False, f"missing key {key!r}"
    if "length" in check and (not isinstance(value, (list, dict)) or len(value) != int(check["length"])):
        return False, f"length != {check['length']}"
    for dotted, size in (check.get("lengths") or {}).items():
        try:
            node = _dig(value, dotted)
        except (KeyError, IndexError, ValueError, TypeError):
            return False, f"missing {dotted}"
        if not isinstance(node, (list, dict)) or len(node) != int(size):
            return False, f"len({dotted}) != {size}"
    for dotted, expected in (check.get("path_equals") or {}).items():
        try:
            got = _dig(value, dotted)
        except (KeyError, IndexError, ValueError, TypeError):
            return False, f"missing {dotted}"
        if got != expected:
            return False, f"{dotted}={got!r} != {expected!r}"
    return True, ""


def _parse_json_text(text: str) -> Any:
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    if not t.startswith(("{", "[")):
        start = min([i for i in (t.find("{"), t.find("[")) if i != -1], default=-1)
        if start != -1:
            t = t[start:]
    return json.loads(t)


#: 기준 판정 — ``judge(criterion, *, reference, request, answer) -> (passed, reason)``.
Judge = Callable[..., Tuple[bool, str]]


def run_check(check: Mapping[str, Any], *, workspace: str, answer: str, judge: Optional[Judge] = None,
              request: str = "") -> CheckResult:
    kind = str(check.get("kind"))
    name = str(check.get("name") or kind)
    fmt = bool(check.get("format", False))

    def res(ok: bool, detail: str = "") -> CheckResult:
        return CheckResult(name=name, kind=kind, passed=bool(ok), detail=detail, format=fmt)

    try:
        if kind == "file_exists":
            return res(_read(workspace, check["path"]) is not None, "")
        if kind in ("file_contains", "file_not_contains"):
            text = _read(workspace, check["path"])
            if text is None:
                return res(False, "file missing")
            hit = _match(text, check)
            return res(hit if kind == "file_contains" else not hit)
        if kind == "file_json":
            text = _read(workspace, check["path"])
            if text is None:
                return res(False, "file missing")
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                return res(False, f"invalid json: {exc}")
            ok, detail = _json_checks(value, check)
            return res(ok, detail)
        if kind == "file_csv":
            text = _read(workspace, check["path"])
            if text is None:
                return res(False, "file missing")
            rows = list(csv.reader(io.StringIO(text)))
            rows = [r for r in rows if r]
            if not rows:
                return res(False, "empty csv")
            header, body = rows[0], rows[1:]
            if "header" in check and header != list(check["header"]):
                return res(False, f"header {header} != {check['header']}")
            if any(len(r) != len(header) for r in body):
                return res(False, "ragged rows")
            if "rows" in check and len(body) != int(check["rows"]):
                return res(False, f"rows {len(body)} != {check['rows']}")
            if "unique" in check:
                col = header.index(check["unique"])
                vals = [r[col] for r in body]
                if len(vals) != len(set(vals)):
                    return res(False, f"duplicate {check['unique']}")
            for col_name, allowed in (check.get("allowed") or {}).items():
                col = header.index(col_name)
                bad = [r[col] for r in body if r[col] not in allowed]
                if bad:
                    return res(False, f"{col_name} has {bad[:3]}")
            for cell in check.get("cells") or ():
                col = header.index(cell["col"])
                row = body[int(cell["row"])]
                if row[col] != str(cell["value"]):
                    return res(False, f"cell {cell} got {row[col]!r}")
            return res(True)
        if kind == "file_unchanged":
            text = _read(workspace, check["path"])
            if text is None:
                return res(False, "file missing")
            return res(hashlib.sha256(text.encode()).hexdigest() == check["sha256"])
        if kind in ("answer_contains", "answer_not_contains"):
            hit = _match(answer or "", check)
            return res(hit if kind == "answer_contains" else not hit)
        if kind == "answer_json":
            try:
                value = _parse_json_text(answer or "")
            except (json.JSONDecodeError, ValueError) as exc:
                return res(False, f"invalid json: {exc}")
            ok, detail = _json_checks(value, check)
            return res(ok, detail)
        if kind == "answer_criteria":
            if judge is None:
                return res(False, "no judge configured")
            if not (answer or "").strip():
                return res(False, "empty answer")
            ok, reason = judge(str(check["criterion"]), reference=str(check.get("reference") or ""),
                               request=request, answer=answer)
            return res(bool(ok), str(reason)[:500])
        if kind == "answer_number":
            nums = re.findall(r"-?\d+(?:\.\d+)?", (answer or "").replace(",", ""))
            target = float(check["value"])
            tol = float(check.get("tol", 0.0))
            return res(any(abs(float(n) - target) <= tol for n in nums), f"numbers {nums[-5:]}")
    except Exception as exc:  # noqa: BLE001 — 검사 정의 오류는 실패로 기록(검증기 자체를 죽이지 않는다)
        return res(False, f"check error: {type(exc).__name__}: {exc}")
    return res(False, f"unknown check kind {kind!r}")


def verify(checks: Sequence[Mapping[str, Any]], *, workspace: str, answer: str, judge: Optional[Judge] = None,
           request: str = "") -> VerifierResult:
    results = tuple(run_check(c, workspace=workspace, answer=answer, judge=judge, request=request) for c in checks)
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    fmt = [r for r in results if r.format]
    valid = all(r.passed for r in fmt) if fmt else None
    required = [r for r, c in zip(results, checks) if c.get("required")]
    no_sub = bool(required) and all(not r.passed for r in required) and not (answer or "").strip()
    return VerifierResult(
        reward=(passed / total) if total else 0.0,
        weight=float(total) if total else 1.0,
        valid_output=valid,
        no_submission=no_sub,
        checks=results,
        fail_class="ok" if passed == total else "wrong_output",
    )
