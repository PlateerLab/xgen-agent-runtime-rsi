"""내장 평가 스위트 ``xgen-core`` 생성기 — 업무 에이전트 하네스를 재는 결정적 과제 모음.

Harness-Bench 대신 쓰는 첫 진화 인스턴스(설계 34 문서 I-1 자리). 과제는 XGEN 업무 에이전트가 실제로 하는 일의
모양(데이터 파일 변환·문서에서 필드 뽑기·여러 파일 종합·기존 파일 정밀 수정·형식 계약·오류 복구·한국어 업무 문서)을
본뜨되, 정답은 **코드로 계산**해 검사에 넣는다(사람 손 정답 없음). 실행: ``python -m xgen_rsi.suites.build_xgen_core <out_dir>``.

분할은 고정이다(evolve / heldout / smoke). 범주가 두 분할에 고르게 퍼지도록 범주마다 나눈다 — evolve 만 보고 맞춘
하네스가 heldout 에서 무너지는지(과적합)를 볼 수 있게.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import random
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Tuple

from xgen_rsi.evolve.tasks import TaskSpec, write_suite

SUITE_NAME = "xgen-core"


def _csv(rows: List[List[Any]]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _write(ws: str, rel: str, text: str) -> None:
    path = os.path.join(ws, rel)
    os.makedirs(os.path.dirname(path) or ws, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


@dataclass(frozen=True)
class Built:
    """과제 + 오라클(정답을 작업 공간에 쓰고 최종 답을 돌려줌) — 검증기 자체를 검증하는 데 쓴다."""

    spec: TaskSpec
    oracle: Callable[[str], str]


# ── 범주 A: 데이터 파일 변환 ───────────────────────────────────────────────


def cat_data(rng: random.Random, idx: int) -> "Built":
    regions = ["서울", "부산", "대구", "광주", "대전"]
    products = ["노트북", "모니터", "키보드", "마우스", "도킹스테이션"]
    rows = [["order_id", "region", "product", "qty", "unit_price"]]
    for i in range(18 + idx):
        rows.append([f"O{1000 + i}", rng.choice(regions), rng.choice(products), rng.randint(1, 9), rng.choice([12000, 35000, 89000, 240000])])
    target = rng.choice(regions)
    agg: Dict[str, Tuple[int, int]] = {}
    for r in rows[1:]:
        if r[1] != target:
            continue
        q, amt = agg.get(r[2], (0, 0))
        agg[r[2]] = (q + int(r[3]), amt + int(r[3]) * int(r[4]))
    expected = sorted(agg.items(), key=lambda kv: (-kv[1][1], kv[0]))
    out_rows = len(expected)
    cells = [{"row": i, "col": "product", "value": p} for i, (p, _) in enumerate(expected)]
    cells += [{"row": i, "col": "revenue", "value": str(v[1])} for i, (_, v) in enumerate(expected)]
    def oracle(ws: str) -> str:
        out = [["product", "total_qty", "revenue"]] + [[p, q, a] for p, (q, a) in expected]
        _write(ws, "summary.csv", _csv(out))
        return "summary.csv 를 저장했습니다."

    return Built(oracle=oracle, spec=TaskSpec(
        id=f"data-region-revenue-{idx}",
        prompt=(
            f"orders.csv 에서 지역이 '{target}' 인 주문만 골라 제품별로 합계를 내 주세요. "
            "결과는 summary.csv 로 저장하고 열은 정확히 product,total_qty,revenue 순서여야 합니다. "
            "revenue 는 qty×unit_price 의 합이고, revenue 가 큰 순서(같으면 제품명 오름차순)로 정렬하세요."
        ),
        files={"orders.csv": _csv(rows)},
        checks=(
            {"kind": "file_exists", "path": "summary.csv", "required": True},
            {"kind": "file_csv", "path": "summary.csv", "header": ["product", "total_qty", "revenue"], "format": True, "name": "header"},
            {"kind": "file_csv", "path": "summary.csv", "header": ["product", "total_qty", "revenue"], "rows": out_rows, "name": "row-count"},
            {"kind": "file_csv", "path": "summary.csv", "header": ["product", "total_qty", "revenue"], "cells": cells, "name": "values-and-order"},
            {"kind": "file_unchanged", "path": "orders.csv", "sha256": _sha(_csv(rows)), "name": "input-preserved"},
        ),
        tags=("data",),
    ))


# ── 범주 B: 문서에서 필드 뽑기 → JSON ─────────────────────────────────────


def cat_extract(rng: random.Random, idx: int) -> "Built":
    vendors = ["(주)한빛솔루션", "대성테크", "누리시스템", "미래정보통신", "새벽데이터"]
    vendor = rng.choice(vendors)
    amount = rng.randint(120, 980) * 10000
    due = f"2026-{rng.randint(10, 12):02d}-{rng.randint(1, 28):02d}"
    po = f"PO-2026-{rng.randint(100, 999)}"
    items = rng.sample(["서버 유지보수", "라이선스 갱신", "보안 점검", "데이터 이관", "교육 지원"], 3)
    doc = (
        f"견적 및 발주 확인서\n\n발주번호: {po}\n공급사: {vendor}\n\n"
        f"본 문서는 아래 항목에 대한 발주를 확인합니다.\n"
        + "".join(f"- {it}\n" for it in items)
        + f"\n총 금액(부가세 포함): {amount:,}원\n납기일: {due}\n\n담당자 확인 후 회신 바랍니다.\n"
    )
    def oracle(ws: str) -> str:
        _write(ws, "po.json", json.dumps({"po_number": po, "vendor": vendor, "amount_krw": amount, "due_date": due, "items": items}, ensure_ascii=False))
        return "po.json 저장 완료"

    return Built(oracle=oracle, spec=TaskSpec(
        id=f"extract-po-{idx}",
        prompt=(
            "po.txt 를 읽고 다음 키를 가진 JSON 을 po.json 으로 저장하세요: "
            "po_number(문자열), vendor(문자열), amount_krw(정수), due_date(YYYY-MM-DD 문자열), items(문자열 배열, 문서 순서대로)."
        ),
        files={"po.txt": doc},
        checks=(
            {"kind": "file_json", "path": "po.json", "keys": ["po_number", "vendor", "amount_krw", "due_date", "items"], "format": True, "required": True, "name": "schema"},
            {"kind": "file_json", "path": "po.json", "path_equals": {"po_number": po, "vendor": vendor}, "name": "ids"},
            {"kind": "file_json", "path": "po.json", "path_equals": {"amount_krw": amount}, "name": "amount-integer"},
            {"kind": "file_json", "path": "po.json", "path_equals": {"due_date": due}, "name": "due-date"},
            {"kind": "file_json", "path": "po.json", "path_equals": {"items": items}, "name": "items-in-order"},
        ),
        tags=("extract",),
    ))


# ── 범주 C: 여러 파일 종합 보고 ───────────────────────────────────────────


def cat_synthesis(rng: random.Random, idx: int) -> "Built":
    teams = ["영업", "개발", "운영", "재무"]
    files: Dict[str, str] = {}
    risks = []
    totals = {}
    for t in teams:
        spent = rng.randint(30, 95)
        risk = rng.choice(["일정 지연", "인력 부족", "예산 초과", "없음"])
        totals[t] = spent
        if risk != "없음":
            risks.append((t, risk))
        files[f"reports/{t}.md"] = f"# {t}팀 주간 보고\n\n- 예산 집행률: {spent}%\n- 주요 위험: {risk}\n- 다음 주 계획: 정기 점검\n"
    top = max(totals.items(), key=lambda kv: kv[1])
    checks: List[Dict[str, Any]] = [
        {"kind": "file_exists", "path": "summary.md", "required": True},
        {"kind": "file_contains", "path": "summary.md", "regex": r"^#+\s*요약", "format": True, "name": "has-summary-heading"},
        {"kind": "file_contains", "path": "summary.md", "regex": r"^#+\s*위험", "format": True, "name": "has-risk-heading"},
        {"kind": "file_contains", "path": "summary.md", "text": f"{top[0]}", "name": "mentions-top-team"},
        {"kind": "file_contains", "path": "summary.md", "regex": rf"{top[0]}[^\n]*{top[1]}%", "name": "top-team-rate"},
    ]
    for t, r in risks:
        checks.append({"kind": "file_contains", "path": "summary.md", "regex": rf"{t}[^\n]*{r}", "name": f"risk-{t}"})
    def oracle(ws: str) -> str:
        body = f"## 요약\n{top[0]} {top[1]}%\n\n## 위험\n" + "".join(f"{t}: {r}\n" for t, r in risks)
        _write(ws, "summary.md", body)
        return "summary.md 작성 완료"

    return Built(oracle=oracle, spec=TaskSpec(
        id=f"synth-weekly-{idx}",
        prompt=(
            "reports 폴더의 팀별 주간 보고를 모두 읽고 summary.md 를 작성하세요. "
            "'## 요약' 절에는 예산 집행률이 가장 높은 팀과 그 비율(예: 개발 87%)을 한 줄로, "
            "'## 위험' 절에는 위험이 있는 팀마다 '팀명: 위험' 한 줄씩 적으세요(위험이 '없음'인 팀은 빼세요)."
        ),
        files=files,
        checks=tuple(checks),
        tags=("synthesis",),
    ))


# ── 범주 D: 기존 파일 정밀 수정 ───────────────────────────────────────────


def cat_edit(rng: random.Random, idx: int) -> "Built":
    port = rng.choice([8080, 8443, 9000, 3000])
    new_port = port + rng.choice([1, 10, 100])
    workers = rng.randint(2, 8)
    config = {
        "service": {"name": "billing-api", "port": port, "workers": workers},
        "database": {"host": "db.internal", "pool": 10},
        "features": {"audit": True, "beta_ui": False},
    }
    text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    notes = "운영 메모: 이 파일은 수정하지 마세요.\n"
    def oracle(ws: str) -> str:
        new = json.loads(text)
        new["service"]["port"] = new_port
        new["features"]["beta_ui"] = True
        _write(ws, "config.json", json.dumps(new, ensure_ascii=False, indent=2) + "\n")
        return "config.json 을 수정했습니다."

    return Built(oracle=oracle, spec=TaskSpec(
        id=f"edit-config-{idx}",
        prompt=(
            f"config.json 에서 service.port 를 {new_port} 로, features.beta_ui 를 true 로 바꾸세요. "
            "다른 값은 그대로 두고, notes.txt 는 건드리지 마세요."
        ),
        files={"config.json": text, "notes.txt": notes},
        checks=(
            {"kind": "file_json", "path": "config.json", "path_equals": {"service.port": new_port}, "format": True, "required": True, "name": "port"},
            {"kind": "file_json", "path": "config.json", "path_equals": {"features.beta_ui": True}, "name": "beta-flag"},
            {"kind": "file_json", "path": "config.json", "path_equals": {"service.workers": workers, "database.pool": 10, "features.audit": True}, "name": "others-kept"},
            {"kind": "file_unchanged", "path": "notes.txt", "sha256": _sha(notes), "name": "notes-untouched"},
        ),
        tags=("edit",),
    ))


# ── 범주 E: 형식 계약(구조화 답) ───────────────────────────────────────────


def cat_format(rng: random.Random, idx: int) -> "Built":
    names = rng.sample(["김민준", "이서연", "박지훈", "최유진", "정하늘", "강도윤"], 4)
    scores = {n: rng.randint(55, 99) for n in names}
    passed = sorted([n for n, s in scores.items() if s >= 70])
    text = "".join(f"{n}: {s}점\n" for n, s in scores.items())
    def oracle(ws: str) -> str:
        return json.dumps({"passed": passed, "count": len(passed)}, ensure_ascii=False)

    return Built(oracle=oracle, spec=TaskSpec(
        id=f"format-json-answer-{idx}",
        prompt=(
            "scores.txt 를 읽고 70점 이상인 사람의 이름을 가나다순으로 정리해 답하세요. "
            "답은 반드시 JSON 한 개로만 하세요: {\"passed\": [이름...], \"count\": 정수}. 설명을 붙이지 마세요."
        ),
        files={"scores.txt": text},
        checks=(
            {"kind": "answer_json", "keys": ["passed", "count"], "format": True, "required": True, "name": "json-shape"},
            {"kind": "answer_json", "path_equals": {"passed": passed}, "name": "names-sorted"},
            {"kind": "answer_json", "path_equals": {"count": len(passed)}, "name": "count"},
            {"kind": "answer_contains", "regex": r"\A\s*(\{|```)", "name": "no-prose-before-json"},
        ),
        tags=("format",),
    ))


# ── 범주 F: 계산(숫자 답) ─────────────────────────────────────────────────


def cat_compute(rng: random.Random, idx: int) -> "Built":
    rows = [["date", "category", "amount"]]
    total = 0
    cat = rng.choice(["교통", "식비", "숙박", "회의"])
    for d in range(1, 16):
        c = rng.choice(["교통", "식비", "숙박", "회의"])
        a = rng.randint(3, 120) * 1000
        rows.append([f"2026-09-{d:02d}", c, a])
        if c == cat:
            total += a
    def oracle(ws: str) -> str:
        return f"합계는 다음과 같습니다.\n{total}"

    return Built(oracle=oracle, spec=TaskSpec(
        id=f"compute-expense-{idx}",
        prompt=f"expenses.csv 에서 category 가 '{cat}' 인 지출의 합계를 원 단위 정수로 알려 주세요. 마지막 줄에 숫자만 적으세요.",
        files={"expenses.csv": _csv(rows)},
        checks=(
            {"kind": "answer_number", "value": total, "tol": 0, "required": True, "name": "total"},
            {"kind": "answer_contains", "regex": r"\d[\d,]*\s*\Z", "format": True, "name": "ends-with-number"},
        ),
        tags=("compute",),
    ))


# ── 범주 G: 한국어 업무 문서 → 실행 항목 ──────────────────────────────────


def cat_minutes(rng: random.Random, idx: int) -> "Built":
    people = rng.sample(["김과장", "이대리", "박팀장", "최주임", "정차장"], 3)
    tasks = rng.sample(["견적서 재발송", "계약서 검토", "데모 일정 확정", "보안 서약서 수거", "예산안 수정"], 3)
    days = rng.sample(["10월 6일", "10월 8일", "10월 10일", "10월 13일"], 3)
    lines = ["주간 회의록 (요약 전 원문)", "", "참석: " + ", ".join(people), ""]
    for p, t, d in zip(people, tasks, days):
        lines.append(f"- {p}: {t} 건은 제가 맡겠습니다. {d}까지 마무리하겠습니다.")
    lines.append("- 기타: 다음 회의는 격주로 진행.")
    expected = [{"owner": p, "task": t, "due": d} for p, t, d in zip(people, tasks, days)]
    checks: List[Dict[str, Any]] = [
        {"kind": "file_json", "path": "actions.json", "length": 3, "format": True, "required": True, "name": "three-items"},
    ]
    for i, e in enumerate(expected):
        checks.append({"kind": "file_json", "path": "actions.json", "path_equals": {f"{i}.owner": e["owner"], f"{i}.due": e["due"]}, "name": f"item-{i}-owner-due"})
        checks.append({"kind": "file_contains", "path": "actions.json", "text": e["task"], "name": f"item-{i}-task"})
    def oracle(ws: str) -> str:
        _write(ws, "actions.json", json.dumps(expected, ensure_ascii=False))
        return "actions.json 저장"

    return Built(oracle=oracle, spec=TaskSpec(
        id=f"minutes-actions-{idx}",
        prompt=(
            "minutes.txt 회의록에서 실행 항목을 뽑아 actions.json 에 배열로 저장하세요. "
            "각 항목은 {\"owner\": 담당자, \"task\": 할 일, \"due\": 기한(원문 표기 그대로)} 이고 회의록에 나온 순서를 지키세요. "
            "'기타' 줄은 실행 항목이 아닙니다."
        ),
        files={"minutes.txt": "\n".join(lines) + "\n"},
        checks=tuple(checks),
        tags=("korean-business",),
    ))


# ── 범주 H: 오류 복구(파일 이름이 다름) ───────────────────────────────────


def cat_recovery(rng: random.Random, idx: int) -> "Built":
    real = rng.choice(["customer_list_2026.csv", "customers_final.csv", "client-list.csv"])
    rows = [["customer", "tier"]] + [[f"C{i:03d}", rng.choice(["gold", "silver", "bronze"])] for i in range(12)]
    gold = sum(1 for r in rows[1:] if r[1] == "gold")
    def oracle(ws: str) -> str:
        _write(ws, "gold.txt", f"{gold}\n")
        return f"{real} 에서 세었습니다."

    return Built(oracle=oracle, spec=TaskSpec(
        id=f"recovery-missing-file-{idx}",
        prompt=(
            "customers.csv 에서 tier 가 gold 인 고객 수를 세어 gold.txt 에 숫자만 저장해 주세요. "
            "(파일 이름이 조금 다를 수 있습니다 — 작업 폴더에서 고객 목록 파일을 찾아 쓰세요.)"
        ),
        files={real: _csv(rows), "README.txt": "고객 목록 파일이 이 폴더 어딘가에 있습니다.\n"},
        checks=(
            {"kind": "file_exists", "path": "gold.txt", "required": True},
            {"kind": "file_contains", "path": "gold.txt", "regex": rf"^\s*{gold}\s*$", "format": True, "name": "count-only"},
        ),
        tags=("recovery",),
    ))


CATEGORIES = [cat_data, cat_extract, cat_synthesis, cat_edit, cat_format, cat_compute, cat_minutes, cat_recovery]


def build_with_oracles(seed: int = 20261001, per_category: int = 4) -> Tuple[List[Built], Dict[str, List[str]]]:
    rng = random.Random(seed)
    built: List[Built] = []
    evolve: List[str] = []
    heldout: List[str] = []
    for cat in CATEGORIES:
        group = [cat(rng, i) for i in range(per_category)]
        built.extend(group)
        made = [b.spec for b in group]
        # 범주마다 앞 3개 evolve, 마지막 1개 heldout(범주가 양쪽에 고르게)
        evolve.extend(t.id for t in made[:-1])
        heldout.append(made[-1].id)
    smoke = [built[0].spec.id, built[per_category].spec.id]
    return built, {"evolve": evolve, "heldout": heldout, "smoke": smoke}


def build(seed: int = 20261001, per_category: int = 4) -> Tuple[List[TaskSpec], Dict[str, List[str]]]:
    built, splits = build_with_oracles(seed, per_category)
    return [b.spec for b in built], splits


def main(argv: List[str]) -> None:
    out = argv[1] if len(argv) > 1 else "xgen_core"
    tasks, splits = build()
    write_suite(SUITE_NAME, tasks, splits, out)
    print(f"wrote {len(tasks)} tasks to {out} (evolve {len(splits['evolve'])}, heldout {len(splits['heldout'])})")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv)
