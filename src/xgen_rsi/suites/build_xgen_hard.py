"""내장 평가 스위트 ``xgen-hard`` 생성기 — 최신 모델도 한 번에 다 맞히기 어려운 업무 과제.

``xgen-core`` 는 과제마다 규칙이 하나라 최상위 모델이 만점을 낸다(천장 — 하네스가 나아질 여지를 잴 수 없다).
``xgen-hard`` 는 하네스가 실제로 영향을 주는 지점을 겨눈다:

* **양** — 눈으로 세면 틀리기 쉬운 규모(주문 90행+, 응답 55행+, 고객 70행+). 도구(Grep 개수 세기 등)를 쓰고
  중간 결과를 검산하는 습관이 점수를 가른다.
* **규칙 문서** — 정정 행·상태·표기 정규화·반품, 부가세 포함 합계, 중복 응답·고객의 마지막 행 기준.
* **함정** — 출장일 뒤에 시행되는 부칙(적용하면 틀림), 연기된 일정, 두 번째 보류 항목, 조건부 변경, 폐기본·지난 분기 파일.
* **두 단계 정합성**, **스키마 준수**, **상대 날짜**.

정답은 전부 **코드로 계산**하고(사람 손 정답 없음), 과제마다 오라클이 만점·빈 답이 0.5 이하임을 테스트로 고정한다.
실행: ``python -m xgen_rsi.suites.build_xgen_hard <out_dir>`` 또는 ``rsi suite build OUT --suite xgen-hard``.
분할은 ``xgen-core`` 와 같다 — 범주마다 앞 3개 evolve, 마지막 1개 heldout, smoke 2개.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import re
import sys
from typing import Any, Dict, List, Tuple

from xgen_rsi.evolve.tasks import TaskSpec, write_suite
from xgen_rsi.suites.build_xgen_core import Built, _csv, _sha, _write

SUITE_NAME = "xgen-hard"
MAX_ITERATIONS = 30
_WD = "월화수목금토일"


def _won(n: int, style: int) -> str:
    """같은 금액의 여러 표기 — 규칙 문서가 정규화를 요구한다."""
    return [f"{n}", f"{n:,}", f"{n:,}원", f"₩{n:,}"][style % 4]


def _re(text: str) -> str:
    return re.escape(text)


# ── A: 규칙 문서대로 지저분한 데이터 집계 ─────────────────────────────────


def cat_spec_aggregate(rng: random.Random, idx: int) -> Built:
    aliases = {
        "서울": ["서울", "서울시", "서울특별시", " 서울"],
        "부산": ["부산", "부산시", "부산광역시"],
        "대구": ["대구", "대구시", "대구광역시"],
        "광주": ["광주", "광주시", "광주광역시"],
        "대전": ["대전", "대전시", " 대전 "],
        "울산": ["울산", "울산시", "울산광역시"],
    }
    statuses = ["완료"] * 7 + ["취소", "보류", "완료 ", "환불대기"]
    prices = [12000, 35000, 89000, 240000, 5500, 17800]
    base = dt.date(2026, 9, 1)
    rows: List[List[Any]] = []
    n = 90 + idx * 8
    for i in range(n):
        region = rng.choice(list(aliases))
        rows.append([f"O-{2000 + i}", (base + dt.timedelta(days=rng.randint(0, 25))).isoformat(),
                     rng.choice(aliases[region]), rng.choice(statuses),
                     rng.choice([1, 2, 3, 4, 5, 6, 7, -1, -2]), rng.choice(prices), rng.randint(0, 3)])
    for j in rng.sample(range(n), 8):  # 정정 행 — 늦은 날짜가 이긴다
        orig = rows[j]
        later = (dt.date.fromisoformat(orig[1]) + dt.timedelta(days=rng.randint(1, 4))).isoformat()
        rows.append([orig[0], later, orig[2], rng.choice(["완료", "취소", "완료 "]), rng.choice([1, 2, 3, 4, 8]),
                     orig[5], rng.randint(0, 3)])
    rng.shuffle(rows)
    header = ["order_id", "date", "region", "status", "qty", "unit_price"]
    text_rows = [header] + [[r[0], r[1], r[2], r[3], r[4], _won(r[5], r[6])] for r in rows]
    latest: Dict[str, Tuple[str, int, List[Any]]] = {}
    for pos, r in enumerate(rows):
        cur = latest.get(r[0])
        if cur is None or (r[1], pos) > (cur[0], cur[1]):
            latest[r[0]] = (r[1], pos, r)
    duplicates = len(rows) - len(latest)
    canon = {a.strip(): k for k, vs in aliases.items() for a in vs}
    excluded = 0
    agg: Dict[str, List[int]] = {}
    for _, _, r in latest.values():
        if r[3].strip() != "완료":
            excluded += 1
            continue
        slot = agg.setdefault(canon[r[2].strip()], [0, 0])
        slot[0] += 1
        slot[1] += int(r[4]) * int(r[5])
    ranked = sorted(agg.items(), key=lambda kv: (-kv[1][1], kv[0]))
    spec = (
        "# 주문 집계 규칙\n\n"
        "1. 같은 order_id 가 여러 번 나오면 **date 가 가장 늦은 행 하나만** 남긴다(날짜가 같으면 파일에서 아래쪽 행).\n"
        "2. 남은 행 중 status 가 '완료'(앞뒤 공백 무시)인 것만 집계한다. 나머지('취소'·'보류'·'환불대기' 등)는 제외 건수로 센다.\n"
        "3. region 은 앞뒤 공백을 지우고 '시'·'특별시'·'광역시' 표기를 기본 지명으로 합친다(예: '서울특별시' → '서울').\n"
        "4. unit_price 는 '12,000원'·'₩12,000'·'12,000' 처럼 적혀 있어도 모두 같은 정수 12000 이다.\n"
        "5. qty 가 음수인 행은 반품이다. 그대로 qty×unit_price 로 더한다(매출이 줄어든다).\n"
        "6. revenue = Σ qty×unit_price, orders = 집계에 들어간 행 수.\n"
    )
    out = {"regions": [{"region": r, "orders": v[0], "revenue": v[1]} for r, v in ranked],
           "excluded": {"not_completed": excluded, "duplicates": duplicates}}

    def oracle(ws: str) -> str:
        _write(ws, "report.json", json.dumps(out, ensure_ascii=False))
        return "report.json 저장"

    checks: List[Dict[str, Any]] = [
        {"kind": "file_exists", "path": "report.json", "required": True},
        {"kind": "file_json", "path": "report.json", "keys": ["regions", "excluded"], "format": True, "name": "shape"},
        {"kind": "file_json", "path": "report.json", "lengths": {"regions": len(ranked)}, "name": "region-count"},
    ]
    for i, (r, v) in enumerate(ranked):
        checks.append({"kind": "file_json", "path": "report.json",
                       "path_equals": {f"regions.{i}.region": r, f"regions.{i}.revenue": v[1]}, "name": f"rank-{i}-revenue"})
    checks += [
        {"kind": "file_json", "path": "report.json",
         "path_equals": {f"regions.{i}.orders": v[0] for i, (_, v) in enumerate(ranked)}, "name": "order-counts"},
        {"kind": "file_json", "path": "report.json", "path_equals": {"excluded.not_completed": excluded}, "name": "excluded-not-completed"},
        {"kind": "file_json", "path": "report.json", "path_equals": {"excluded.duplicates": duplicates}, "name": "excluded-duplicates"},
        {"kind": "file_unchanged", "path": "orders.csv", "sha256": _sha(_csv(text_rows)), "name": "input-preserved"},
    ]
    return Built(oracle=oracle, spec=TaskSpec(
        id=f"spec-aggregate-{idx}",
        prompt=(
            "orders.csv 를 SPEC.md 의 규칙대로 지역별로 집계해 report.json 으로 저장하세요. 형식: "
            '{"regions": [{"region": 지역, "orders": 건수, "revenue": 매출(정수)}, ...], '
            '"excluded": {"not_completed": 완료가 아니라 뺀 건수, "duplicates": 중복으로 버린 행 수}}. '
            "regions 는 revenue 가 큰 순서(같으면 지역명 오름차순)로 정렬하세요. orders.csv 는 고치지 마세요."
        ),
        files={"orders.csv": _csv(text_rows), "SPEC.md": spec},
        checks=tuple(checks),
        max_iterations=MAX_ITERATIONS,
        tags=("spec-aggregate",),
    ))


# ── B: 여러 문서 대조(장부 vs 청구서 파일들, 부가세 포함 합계) ─────────────


def cat_reconcile(rng: random.Random, idx: int) -> Built:
    vendors = ["한빛솔루션", "대성테크", "누리시스템", "미래정보통신", "새벽데이터", "초록물류", "바른회계", "온새미로"]
    ids = [f"INV-{3100 + idx * 40 + i}" for i in range(18)]
    supply = {i: rng.randint(30, 900) * 1000 for i in ids}
    total = {i: supply[i] + supply[i] // 10 for i in ids}  # 부가세 10% 포함 합계
    vendor_of = {i: rng.choice(vendors) for i in ids}
    missing_in_ledger = rng.sample(ids, 2)
    rest = [i for i in ids if i not in missing_in_ledger]
    mismatch = rng.sample(rest, 3)
    deltas = rng.sample([90, 1000, -10000, 500, -2300], 3)
    no_invoice = [f"INV-{3290 + idx * 2 + j}" for j in range(2)]
    ledger_rows = [["invoice_id", "vendor", "amount"]]
    for i in rest:
        ledger_rows.append([i, vendor_of[i], total[i] + (deltas[mismatch.index(i)] if i in mismatch else 0)])
    for j in no_invoice:
        ledger_rows.append([j, rng.choice(vendors), rng.randint(30, 900) * 1100])
    body_rows = ledger_rows[1:]
    rng.shuffle(body_rows)
    ledger_rows = [ledger_rows[0]] + body_rows
    ledger_amount = {r[0]: r[2] for r in ledger_rows[1:]}
    files: Dict[str, str] = {"ledger.csv": _csv(ledger_rows)}
    for k, i in enumerate(ids):
        sub = rng.choice(["2026-08", "2026-09"])
        if k % 3 == 0:  # 공급가액·부가세·합계로 적힌 청구서 — 대조는 합계로
            body = (f"공급가액: {_won(supply[i], 2)}\n부가세: {_won(supply[i] // 10, 2)}\n합계: {_won(total[i], 2)}\n")
        else:
            body = f"청구 금액(부가세 포함): {_won(total[i], rng.randint(1, 3))}\n"
        files[f"invoices/{sub}/{i}.txt"] = f"청구서\n청구서 번호: {i}\n공급사: {vendor_of[i]}\n{body}"
    for decoy in rng.sample(rest, 2):  # 폐기본 — 규칙상 무시
        files[f"invoices/archive/{decoy}.txt"] = (f"청구서(폐기본)\n청구서 번호: {decoy}\n공급사: {vendor_of[decoy]}\n"
                                                  f"청구 금액(부가세 포함): {_won(total[decoy] + 55000, 1)}\n")
    files["README.md"] = ("청구서는 invoices/<월>/ 아래에 있습니다. invoices/archive/ 는 폐기본이라 대조에서 제외합니다.\n"
                          "장부 금액은 부가세 포함 합계입니다. 공급가액과 부가세가 따로 적힌 청구서는 합계로 비교하세요.\n")
    expected: List[List[str]] = []
    for i in sorted(set(ids) | set(no_invoice)):
        if i in missing_in_ledger:
            expected.append([i, "missing_in_ledger", "", str(total[i])])
        elif i in no_invoice:
            expected.append([i, "missing_invoice", str(ledger_amount[i]), ""])
        elif i in mismatch:
            expected.append([i, "amount_mismatch", str(ledger_amount[i]), str(total[i])])
    header = ["invoice_id", "issue", "ledger_amount", "invoice_amount"]

    def oracle(ws: str) -> str:
        _write(ws, "mismatches.csv", _csv([header] + expected))
        return "mismatches.csv 저장"

    checks: List[Dict[str, Any]] = [
        {"kind": "file_exists", "path": "mismatches.csv", "required": True},
        {"kind": "file_csv", "path": "mismatches.csv", "header": header, "format": True, "name": "header"},
        {"kind": "file_csv", "path": "mismatches.csv", "header": header, "rows": len(expected), "name": "row-count"},
        {"kind": "file_csv", "path": "mismatches.csv", "header": header,
         "allowed": {"issue": ["amount_mismatch", "missing_in_ledger", "missing_invoice"]}, "name": "issue-values"},
    ]
    for k, row in enumerate(expected):
        checks.append({"kind": "file_csv", "path": "mismatches.csv", "header": header, "rows": len(expected),
                       "cells": [{"row": k, "col": c, "value": v} for c, v in zip(header, row)], "name": f"row-{k}"})
    checks.append({"kind": "file_unchanged", "path": "ledger.csv", "sha256": _sha(files["ledger.csv"]), "name": "ledger-preserved"})
    return Built(oracle=oracle, spec=TaskSpec(
        id=f"reconcile-{idx}",
        prompt=(
            "ledger.csv(장부)와 invoices/ 아래의 청구서 파일들을 대조해 어긋난 것만 mismatches.csv 로 저장하세요. "
            "열은 정확히 invoice_id,issue,ledger_amount,invoice_amount 이고, issue 는 amount_mismatch(금액 다름) · "
            "missing_in_ledger(청구서는 있는데 장부에 없음) · missing_invoice(장부에는 있는데 청구서 없음) 중 하나입니다. "
            "금액은 쉼표·'원' 없는 정수로, 해당 쪽이 없으면 빈 칸으로 두세요. invoice_id 오름차순으로 정렬하세요. "
            "README.md 의 안내를 따르세요."
        ),
        files=files,
        checks=tuple(checks),
        max_iterations=MAX_ITERATIONS,
        tags=("reconcile",),
    ))


# ── C: 보류·조건부 항목이 섞인 다중 파일 수정 ─────────────────────────────


def cat_multi_edit(rng: random.Random, idx: int) -> Built:
    port0, port1 = rng.choice([8080, 8000, 8443]), rng.choice([9090, 9443, 7000])
    level1 = rng.choice(["WARN", "ERROR", "DEBUG"])
    ttl0 = rng.choice([300, 600, 900])
    up0, up1 = rng.choice([20, 50, 100]), rng.choice([200, 250, 500])
    feat_new = rng.choice(["export", "audit", "sso"])
    version = f"0.{4 + idx}.1"
    ini = (
        "# 서비스 설정 — 주석은 운영 기록이므로 지우지 마세요\n"
        "[server]\n# 외부 노출 포트\nport = {p}\n# 작업자 수\nworkers = 4\n\n"
        "[log]\n# 로그 수준: DEBUG/INFO/WARN/ERROR\nlevel = INFO\n\n"
        "[cache]\n# 캐시 유지 시간(초)\nttl = {t}\n"
    ).format(p=port0, t=ttl0)
    limits = {"max_upload_mb": up0, "timeout_s": 30, "features": ["search", "share"], "retries": 2}
    changelog = "# 변경 기록\n\n## Unreleased\n\n## 0.4.0\n- 첫 공개\n"
    readme = f"# 서비스 안내\n\n서비스 포트: {port0}\n\n상태 확인 예: curl localhost:{port0}/health\n"
    secrets = "[db]\nuser = app\npassword = CHANGE_ME\n"
    request = (
        "# 변경 요청\n\n"
        f"1. config/app.ini 의 [server] port 를 {port1} 으로 바꾼다.\n"
        f"2. config/app.ini 의 [log] level 을 {level1} 으로 바꾼다.\n"
        f"3. config/limits.json 의 max_upload_mb 를 {up1} 으로 바꾼다.\n"
        f"4. config/limits.json 의 features 목록 끝에 \"{feat_new}\" 를 추가한다.\n"
        "5. (보류) [cache] ttl 을 1200 으로 바꾸는 건은 다음 릴리스로 미룬다 — 이번에는 적용하지 않는다.\n"
        "6. (보류) features 에서 \"share\" 를 빼는 건도 보류한다.\n"
        "7. port 를 바꾼 경우에만 [server] workers 를 8 로 바꾼다.\n"
        "8. docs/README.md 에 적힌 옛 포트 번호를 모두 새 포트로 바꾼다.\n"
        f"9. docs/CHANGELOG.md 의 '## Unreleased' 바로 아래에 '- {version}: 포트 {port1}, 업로드 {up1}MB' 한 줄을 추가한다.\n\n"
        "주의: 주석 줄은 그대로 둔다. config/secrets.example.ini 는 건드리지 않는다.\n"
    )
    files = {"config/app.ini": ini, "config/limits.json": json.dumps(limits, ensure_ascii=False, indent=2) + "\n",
             "docs/CHANGELOG.md": changelog, "docs/README.md": readme, "config/secrets.example.ini": secrets,
             "CHANGE_REQUEST.md": request}
    new_limits = dict(limits, max_upload_mb=up1, features=limits["features"] + [feat_new])
    line = f"- {version}: 포트 {port1}, 업로드 {up1}MB"

    def oracle(ws: str) -> str:
        _write(ws, "config/app.ini", ini.replace(f"port = {port0}", f"port = {port1}").replace("level = INFO", f"level = {level1}")
               .replace("workers = 4", "workers = 8"))
        _write(ws, "config/limits.json", json.dumps(new_limits, ensure_ascii=False, indent=2) + "\n")
        _write(ws, "docs/CHANGELOG.md", changelog.replace("## Unreleased\n", f"## Unreleased\n{line}\n"))
        _write(ws, "docs/README.md", readme.replace(str(port0), str(port1)))
        return "변경 요청 1~4·7~9 를 적용하고 5·6(보류)은 적용하지 않았습니다."

    comments = [c for c in ini.splitlines() if c.startswith("#")]
    checks = (
        {"kind": "file_contains", "path": "config/app.ini", "regex": rf"\[server\][^\[]*?^port\s*=\s*{port1}\s*$", "name": "port", "required": True},
        {"kind": "file_contains", "path": "config/app.ini", "regex": rf"\[log\][^\[]*?^level\s*=\s*{level1}\s*$", "name": "log-level"},
        {"kind": "file_contains", "path": "config/app.ini", "regex": r"\[server\][^\[]*?^workers\s*=\s*8\s*$", "name": "conditional-workers"},
        {"kind": "file_contains", "path": "config/app.ini", "regex": rf"\[cache\][^\[]*?^ttl\s*=\s*{ttl0}\s*$", "name": "deferred-ttl-kept"},
        {"kind": "file_contains", "path": "config/app.ini", "regex": "".join(rf"(?=[\s\S]*^{_re(c)}$)" for c in comments), "name": "comments-kept"},
        {"kind": "file_json", "path": "config/limits.json", "path_equals": {"max_upload_mb": up1}, "format": True, "name": "upload"},
        {"kind": "file_json", "path": "config/limits.json", "path_equals": {"features": new_limits["features"]}, "name": "features-appended-share-kept"},
        {"kind": "file_json", "path": "config/limits.json", "path_equals": {"max_upload_mb": up1, "timeout_s": 30, "retries": 2}, "name": "other-keys-kept"},
        {"kind": "file_contains", "path": "docs/README.md", "regex": rf"서비스 포트: {port1}[\s\S]*localhost:{port1}/health", "name": "readme-ports"},
        {"kind": "file_contains", "path": "docs/CHANGELOG.md", "regex": rf"## Unreleased\s*\n{_re(line)}\s*\n", "name": "changelog-line"},
        {"kind": "file_unchanged", "path": "config/secrets.example.ini", "sha256": _sha(secrets), "name": "secrets-untouched"},
    )
    return Built(oracle=oracle, spec=TaskSpec(
        id=f"multi-edit-{idx}",
        prompt="CHANGE_REQUEST.md 의 변경 요청을 저장소에 적용하세요. 요청서의 보류 항목·조건·주의 사항을 지키세요. 다 하면 무엇을 바꿨는지 한두 줄로 알려 주세요.",
        files=files,
        checks=checks,
        max_iterations=MAX_ITERATIONS,
        tags=("multi-edit",),
    ))


# ── D: 긴 규정 문서 질의(부칙 — 시행일이 출장일 뒤면 적용하지 않는다) ────────


def cat_policy_qa(rng: random.Random, idx: int) -> Built:
    grades = ["사원", "대리", "과장", "차장", "부장"]
    allow = {g: v for g, v in zip(grades, sorted(rng.sample(range(25, 70, 5), 5)))}
    allow = {g: v * 1000 for g, v in allow.items()}
    abroad = {g: v for g, v in zip(grades, sorted(rng.sample(range(40, 120, 10), 5)))}  # USD
    hotel1_old, hotel1_new = rng.choice([150000, 160000]), rng.choice([180000, 190000, 200000])
    hotel2 = rng.choice([100000, 110000, 120000])
    deadline = rng.choice([7, 10, 14, 20])
    apply_days = rng.choice([3, 5, 7])
    approver_low, approver_high = "팀장", rng.choice(["본부장", "실장", "대표이사"])
    threshold = rng.choice([300000, 500000, 1000000])
    fx = rng.choice(["출국일", "귀국일", "정산일"])
    grade = rng.choice(grades[1:4])
    abroad_grade = rng.choice([g for g in grades if g != grade])
    trip_day = dt.date(2026, 10, rng.randint(10, 28))
    future_raise = allow[grade] + 10000
    filler = [
        "본 조의 세부 운영 기준은 경영지원팀이 정하며, 부서장은 소속 직원이 규정을 숙지하도록 안내한다.",
        "출장 중 발생한 비용은 실제 지출에 근거해야 하며, 개인적 용도의 지출은 경비로 인정하지 않는다.",
        "본 규정에서 정하지 않은 사항은 관련 법령과 회사의 일반 원칙에 따른다.",
        "출장자는 출장 목적과 일정이 바뀐 경우 지체 없이 결재권자에게 알린다.",
    ]
    arts = [
        ("목적", "이 규정은 임직원의 국내외 출장과 그에 따른 경비 지급 기준을 정함을 목적으로 한다."),
        ("적용 범위", "이 규정은 정규직·계약직 임직원에게 적용한다."),
        ("용어", "'1급지'는 서울특별시와 부산광역시를, '2급지'는 그 밖의 시를 말한다."),
        ("출장 신청", f"출장자는 출발 {apply_days}일 전까지 전자결재로 출장을 신청한다."),
        ("국내 일비", "국내 출장 일비는 직급별로 다음과 같다. " + ", ".join(f"{g} {v:,}원" for g, v in allow.items()) + "."),
        ("해외 일비", "해외 출장 일비는 직급별로 다음과 같다(미화). " + ", ".join(f"{g} {v}달러" for g, v in abroad.items()) + "."),
        ("교통비", "교통비는 실비로 정산하며 항공은 이코노미석을 원칙으로 한다."),
        ("식비", "식비는 일비에 포함되며 별도로 정산하지 않는다."),
        ("숙박비", f"숙박비 상한은 1급지 {hotel1_old:,}원, 2급지 {hotel2:,}원으로 한다."),
        ("경비 승인", f"1건 {threshold:,}원 이하의 경비는 {approver_low}이, 그 초과 경비는 {approver_high}이 승인한다."),
        ("정산", f"출장자는 귀임 후 {deadline}일 이내에 영수증을 첨부해 정산을 신청한다."),
        ("해외 환산", f"해외 출장 경비의 원화 환산은 {fx}의 매매기준율을 적용한다."),
        ("보험", "해외 출장자는 회사가 지정한 여행자 보험에 가입한다."),
        ("안전", "출장자는 현지 안전 정보를 확인하고 비상 연락망을 유지한다."),
    ]
    body = "# 출장 및 경비 규정\n\n" + "\n\n".join(
        f"## 제{i + 1}조({t})\n{c}\n{filler[i % 4]}" for i, (t, c) in enumerate(arts))
    body += (
        "\n\n## 부칙\n"
        "1. 이 규정은 2026년 1월 1일부터 시행한다.\n"
        f"2. 제9조의 숙박비 상한 중 1급지 금액은 2026년 10월 1일 출장분부터 {hotel1_new:,}원으로 한다.\n"
        f"3. 제5조의 {grade} 국내 일비는 2027년 1월 1일 출장분부터 {future_raise:,}원으로 한다.\n"
    )
    questions = (
        "# 질문 (모든 질문은 출장일 기준으로 그때 시행 중인 규정에 따라 답한다)\n\n"
        f"- q1: {trip_day.isoformat()} 국내 출장의 {grade} 일비는 얼마인가? (원, 정수)\n"
        f"- q2: {trip_day.isoformat()} 부산 출장의 1박 숙박비 상한은 얼마인가? (원, 정수)\n"
        "- q3: 영수증 첨부 정산 신청 기한은 귀임 후 며칠 이내인가? (정수)\n"
        f"- q4: 1건 {threshold + 10000:,}원짜리 경비의 승인권자는 누구인가? (직책명만)\n"
        "- q5: 해외 출장 경비 원화 환산 기준일은? (규정의 표현 그대로)\n"
        f"- q6: {abroad_grade} 해외 출장 일비는 몇 달러인가? (정수)\n"
        f"- q7: {trip_day.isoformat()} 대전 출장의 1박 숙박비 상한은 얼마인가? (원, 정수)\n"
        "- q8: 출장 신청은 출발 며칠 전까지 해야 하는가? (정수)\n"
    )
    answers = {"q1": allow[grade], "q2": hotel1_new, "q3": deadline, "q4": approver_high, "q5": fx,
               "q6": abroad[abroad_grade], "q7": hotel2, "q8": apply_days}

    def oracle(ws: str) -> str:
        _write(ws, "answers.json", json.dumps(answers, ensure_ascii=False))
        return "answers.json 저장"

    checks = [
        {"kind": "file_exists", "path": "answers.json", "required": True},
        {"kind": "file_json", "path": "answers.json", "keys": list(answers), "format": True, "name": "keys"},
    ] + [{"kind": "file_json", "path": "answers.json", "path_equals": {k: v}, "name": f"{k}-correct"} for k, v in answers.items()]
    return Built(oracle=oracle, spec=TaskSpec(
        id=f"policy-qa-{idx}",
        prompt=("policy.md(출장 및 경비 규정)를 근거로 questions.md 의 질문에 답해 answers.json 으로 저장하세요. "
                '키는 q1~q8 이고, 금액·일수·달러는 쉼표 없는 정수, 직책·기준일은 문자열입니다. 부칙의 시행일을 확인하세요.'),
        files={"policy.md": body, "questions.md": questions},
        checks=tuple(checks),
        max_iterations=MAX_ITERATIONS,
        tags=("policy-qa",),
    ))


# ── E: 스키마 준수 출력 ──────────────────────────────────────────────────


def cat_schema(rng: random.Random, idx: int) -> Built:
    names = ["A4 복사용지", "토너 카트리지", "모니터 암", "무선 마우스", "회의용 마이크", "USB-C 허브", "라벨 프린터",
             "화이트보드 마커", "노트북 거치대", "랜 케이블", "멀티탭", "웹캠", "헤드셋", "스테이플러"]
    picks = rng.sample(names, 12)
    rows = []
    for k, name in enumerate(picks):
        qty = rng.choice([0, 3, 8, 12, 40, 120, 1200, 2500])
        rop = rng.choice([5, 10, 20, 50])
        note = rng.choice(["", "", "정기 발주", "공급 지연", "단종"]) if k else "단종"
        rows.append({"sku": f"SK-{500 + idx * 20 + k}", "name": name, "qty": qty, "rop": rop, "note": note})
    rng.shuffle(rows)
    as_of = dt.date(2026, 9, rng.randint(20, 30))
    wh = rng.choice(["이천 1센터", "평택 물류센터", "김포 허브"])

    def show(q: int) -> str:
        return "없음" if q == 0 else f"{q:,}개"

    table = "| SKU | 품명 | 재고 | 재주문점 | 비고 |\n|---|---|---|---|---|\n" + "".join(
        f"| {r['sku']} | {r['name']} | {show(r['qty'])} | {r['rop']} | {r['note']} |\n" for r in rows)
    doc = (f"# 재고 현황 — {wh}\n\n기준일: {as_of.year}년 {as_of.month}월 {as_of.day}일\n\n{table}\n"
           "재고가 '없음'이면 0개입니다. 비고가 '단종'인 품목은 더 이상 발주하지 않습니다.\n")
    schema = {
        "type": "object", "required": ["warehouse", "as_of", "items", "totals"],
        "properties": {
            "warehouse": {"type": "string"}, "as_of": {"type": "string", "description": "YYYY-MM-DD"},
            "items": {"type": "array", "items": {"type": "object", "required": ["sku", "name", "qty", "status"], "properties": {
                "sku": {"type": "string"}, "name": {"type": "string"}, "qty": {"type": "integer"},
                "status": {"enum": ["active", "low", "out", "discontinued"],
                           "description": "discontinued: 비고가 단종 / out: 단종이 아니고 재고 0 / low: 단종이 아니고 0 < 재고 < 재주문점 / 그 외 active"}}}},
            "totals": {"type": "object", "required": ["skus", "qty", "reorder"], "properties": {
                "skus": {"type": "integer", "description": "단종을 뺀 품목 수"},
                "qty": {"type": "integer", "description": "단종을 뺀 재고 합"},
                "reorder": {"type": "array", "items": {"type": "string"}, "description": "status 가 low 또는 out 인 SKU, 표 순서"}}},
        },
    }

    def status(r: Dict[str, Any]) -> str:
        if r["note"] == "단종":
            return "discontinued"
        if r["qty"] == 0:
            return "out"
        return "low" if r["qty"] < r["rop"] else "active"

    items = [{"sku": r["sku"], "name": r["name"], "qty": r["qty"], "status": status(r)} for r in rows]
    live = [r for r in rows if r["note"] != "단종"]
    out = {"warehouse": wh, "as_of": as_of.isoformat(), "items": items,
           "totals": {"skus": len(live), "qty": sum(r["qty"] for r in live),
                      "reorder": [it["sku"] for it in items if it["status"] in ("low", "out")]}}

    def oracle(ws: str) -> str:
        _write(ws, "inventory.json", json.dumps(out, ensure_ascii=False))
        return "inventory.json 저장"

    checks = [
        {"kind": "file_exists", "path": "inventory.json", "required": True},
        {"kind": "file_json", "path": "inventory.json", "keys": ["warehouse", "as_of", "items", "totals"], "format": True, "name": "required-keys"},
        {"kind": "file_json", "path": "inventory.json", "path_equals": {"warehouse": wh, "as_of": out["as_of"]}, "name": "header-fields"},
        {"kind": "file_json", "path": "inventory.json", "lengths": {"items": len(items)},
         "path_equals": {f"items.{i}.sku": it["sku"] for i, it in enumerate(items)}, "name": "items-order"},
    ] + [{"kind": "file_json", "path": "inventory.json",
          "path_equals": {f"items.{i}.qty": it["qty"], f"items.{i}.status": it["status"]}, "name": f"item-{i}"} for i, it in enumerate(items)] + [
        {"kind": "file_json", "path": "inventory.json", "path_equals": {"totals.skus": out["totals"]["skus"], "totals.qty": out["totals"]["qty"]}, "name": "totals"},
        {"kind": "file_json", "path": "inventory.json", "path_equals": {"totals.reorder": out["totals"]["reorder"]}, "name": "reorder-list"},
    ]
    return Built(oracle=oracle, spec=TaskSpec(
        id=f"schema-out-{idx}",
        prompt="stock.md 의 재고표를 schema.json 에 맞는 JSON 으로 바꿔 inventory.json 으로 저장하세요. items 는 표의 순서를 지키세요.",
        files={"stock.md": doc, "schema.json": json.dumps(schema, ensure_ascii=False, indent=2)},
        checks=tuple(checks),
        max_iterations=MAX_ITERATIONS,
        tags=("schema",),
    ))


# ── F: 한국어 일정 정규화(상대 날짜·연기·중복·취소) ──────────────────────


def cat_calendar(rng: random.Random, idx: int) -> Built:
    base = dt.date(2026, 10, 5) + dt.timedelta(days=rng.randint(0, 2) + idx * 7)  # 월~수 작성
    titles = ["고객사 미팅", "주간 보고", "디자인 리뷰", "분기 결산", "보안 교육", "채용 면접", "예산 회의", "제품 시연", "법무 검토"]
    t = rng.sample(titles, 8)

    def ko_hour(h: int) -> str:
        return f"{'오전' if h < 12 else '오후'} {h if h <= 12 else h - 12}시"

    monday = base - dt.timedelta(days=base.weekday())
    d1 = base + dt.timedelta(days=rng.randint(2, 5))
    d2 = base + dt.timedelta(days=rng.randint(6, 12))
    wd3 = rng.randint(0, 4)
    d3 = monday + dt.timedelta(days=7 + wd3)
    d4 = base + dt.timedelta(days=rng.randint(13, 20))
    d5 = monday + dt.timedelta(days=4)  # 이번 주 금요일
    d6_old = base + dt.timedelta(days=rng.randint(3, 8))
    d6 = d6_old + dt.timedelta(days=rng.randint(2, 5))
    n7 = rng.randint(9, 15)
    d7 = base + dt.timedelta(days=n7)
    dc = base + dt.timedelta(days=rng.randint(3, 9))
    h1, h2, h3, h4 = rng.choice([9, 10, 14, 16]), rng.choice([10, 11]), rng.choice([13, 15, 17]), rng.choice([9, 11])
    h5, h6, h7 = rng.choice([9, 10, 11]), rng.choice([14, 15, 16]), rng.choice([13, 14, 16])
    ev = [(d1, f"{h1:02d}:00", t[0]), (d2, f"{h2:02d}:30", t[1]), (d3, f"{h3:02d}:00", t[2]), (d4, f"{h4:02d}:30", t[3]),
          (d5, f"{h5:02d}:00", t[4]), (d6, f"{h6:02d}:00", t[5]), (d7, f"{h7:02d}:00", t[6])]
    lines = [
        f"# 팀 일정 메모\n\n작성일: {base.year}년 {base.month}월 {base.day}일 ({_WD[base.weekday()]})",
        "('이번 주'·'다음 주'는 작성일이 속한 주(월~일)와 그 바로 다음 주를 뜻합니다.)\n",
        f"- {d1.month}월 {d1.day}일({_WD[d1.weekday()]}) {ko_hour(h1)} {t[0]}",
        f"- {d2.year}.{d2.month:02d}.{d2.day:02d} 오전 {h2}시 반 {t[1]}",
        f"- 다음 주 {_WD[wd3]}요일 {ko_hour(h3)} {t[2]}",
        f"- {d4.month}/{d4.day} {h4:02d}:30 {t[3]}",
        f"- 이번 주 금요일 {ko_hour(h5)} {t[4]}",
        f"- {d6_old.month}월 {d6_old.day}일 {ko_hour(h6)} {t[5]} → (변경) {d6.month}월 {d6.day}일 같은 시각으로 연기",
        f"- 작성일로부터 {n7}일 뒤 {ko_hour(h7)} {t[6]}",
        f"- ~~{dc.month}월 {dc.day}일 {t[7]}~~ (취소됨)",
        f"- (재공지) {d1.month}월 {d1.day}일 {ko_hour(h1)} {t[0]} — 장소만 변경",
    ]
    expected = sorted((d.isoformat(), hm, title) for d, hm, title in ev)
    header = ["date", "time", "title"]

    def oracle(ws: str) -> str:
        _write(ws, "schedule.csv", _csv([header] + [list(e) for e in expected]))
        return "schedule.csv 저장"

    checks = [
        {"kind": "file_exists", "path": "schedule.csv", "required": True},
        {"kind": "file_csv", "path": "schedule.csv", "header": header, "format": True, "name": "header"},
        {"kind": "file_csv", "path": "schedule.csv", "header": header, "rows": len(expected), "name": "row-count(dedup+cancel)"},
        {"kind": "file_not_contains", "path": "schedule.csv", "text": t[7], "name": "cancelled-excluded"},
    ] + [{"kind": "file_csv", "path": "schedule.csv", "header": header,
          "cells": [{"row": k, "col": c, "value": v} for c, v in zip(header, e)], "name": f"event-{k}"} for k, e in enumerate(expected)]
    return Built(oracle=oracle, spec=TaskSpec(
        id=f"calendar-{idx}",
        prompt=("notes.md 의 일정을 schedule.csv 로 정리하세요. 열은 date,time,title 이고 date 는 YYYY-MM-DD, time 은 24시간 HH:MM, "
                "title 은 일정 이름만(괄호·장소·변경 설명 없이) 적습니다. 같은 일정이 두 번 나오면 한 번만, 취소된 일정은 빼고, "
                "연기된 일정은 바뀐 날짜로, date·time 오름차순으로 정렬하세요."),
        files={"notes.md": "\n".join(lines) + "\n"},
        checks=tuple(checks),
        max_iterations=MAX_ITERATIONS,
        tags=("calendar",),
    ))


# ── G: 두 단계 산출물(정리본 → 보고서)의 정합성 ─────────────────────────


def cat_pipeline(rng: random.Random, idx: int) -> Built:
    teams = {"개발": ["개발", "개발팀", " 개발 ", "dev"], "영업": ["영업", "영업팀", "sales"], "운영": ["운영", "운영팀", "운영 "],
             "재무": ["재무", "재무팀", "finance"]}
    rows = [["respondent", "team", "score", "comment"]]
    total = 55 + idx * 5
    entries: List[Tuple[str, str, str]] = []
    for i in range(total):
        team = rng.choice(list(teams))
        entries.append((f"R{100 + i}", team, rng.choice(["1", "2", "3", "4", "5", "5", "4", "", "N/A", "6", "4.5"])))
    resubmit = rng.sample(range(total), 5)  # 같은 응답자가 다시 낸 응답 — 마지막 것만 유효
    for j in resubmit:
        rid, team, _ = entries[j]
        entries.append((rid, team, rng.choice(["1", "2", "3", "4", "5"])))
    for rid, team, score in entries:
        rows.append([rid, rng.choice(teams[team]), score, rng.choice(["", "좋음", "보통", "개선 필요"])])
    last: Dict[str, Tuple[str, str]] = {}
    order: List[str] = []
    for rid, team, score in entries:
        if rid not in last:
            order.append(rid)
        last[rid] = (team, score)
    clean = [[rid, last[rid][0], int(last[rid][1])] for rid in order if last[rid][1] in ("1", "2", "3", "4", "5")]
    by: Dict[str, List[int]] = {}
    for _, team, s in clean:
        by.setdefault(team, []).append(s)
    avgs = sorted(((tm, round(sum(v) / len(v) + 1e-9, 1)) for tm, v in by.items()), key=lambda kv: (-kv[1], kv[0]))
    header = ["respondent", "team", "score"]
    report = ("# 설문 결과\n\n## 팀별 평균\n" + "".join(f"{tm}: {a:.1f}\n" for tm, a in avgs)
              + f"\n## 응답 수\n유효 응답 {len(clean)} / 응답자 {len(order)}\n")
    spec = ("# 정리 규칙\n\n1. 같은 respondent 가 여러 번 나오면 **파일에서 마지막 행**만 쓴다.\n"
            "2. score 가 1~5 의 정수인 응답만 유효하다(빈칸·N/A·6·4.5 는 무효).\n"
            "3. team 은 기본 이름(개발·영업·운영·재무)으로 바꾼다 — '팀'·앞뒤 공백 제거, dev→개발, sales→영업, finance→재무.\n")

    def oracle(ws: str) -> str:
        _write(ws, "clean/survey_clean.csv", _csv([header] + clean))
        _write(ws, "report.md", report)
        return "정리본과 보고서를 저장했습니다."

    checks = [
        {"kind": "file_exists", "path": "clean/survey_clean.csv", "required": True},
        {"kind": "file_csv", "path": "clean/survey_clean.csv", "header": header, "format": True, "name": "clean-header"},
        {"kind": "file_csv", "path": "clean/survey_clean.csv", "header": header, "rows": len(clean), "name": "clean-row-count"},
        {"kind": "file_csv", "path": "clean/survey_clean.csv", "header": header, "unique": "respondent",
         "allowed": {"team": list(teams), "score": ["1", "2", "3", "4", "5"]}, "name": "clean-values"},
        {"kind": "file_exists", "path": "report.md", "required": True},
        {"kind": "file_contains", "path": "report.md", "regex": r"## 팀별 평균\s*\n" + r"\s*\n".join(_re(f"{tm}: {a:.1f}") for tm, a in avgs),
         "name": "averages-in-order"},
        {"kind": "file_contains", "path": "report.md", "regex": rf"유효 응답 {len(clean)} / 응답자 {len(order)}", "name": "counts-line"},
    ]
    return Built(oracle=oracle, spec=TaskSpec(
        id=f"pipeline-{idx}",
        prompt=("두 단계로 처리하세요. (1) raw/survey.csv 를 RULES.md 대로 정리해 clean/survey_clean.csv(열: respondent,team,score, "
                "응답자가 처음 나온 순서)로 저장. (2) 그 정리본으로 report.md 를 작성: '## 팀별 평균' 절에 '팀: 평균'(소수 첫째 자리, "
                "반올림) 한 줄씩 평균이 높은 순서(같으면 팀명 오름차순)로, '## 응답 수' 절에 '유효 응답 N / 응답자 M' 한 줄 "
                "(M 은 중복을 뺀 응답자 수)."),
        files={"raw/survey.csv": _csv(rows), "RULES.md": spec},
        checks=tuple(checks),
        max_iterations=MAX_ITERATIONS,
        tags=("pipeline",),
    ))


# ── H: 미끼 파일과 중복이 있는 복구 ──────────────────────────────────────


def cat_recovery_hard(rng: random.Random, idx: int) -> Built:
    kor = {"gold": "골드", "silver": "실버", "bronze": "브론즈"}

    def table(n: int, dupes: int) -> Tuple[str, Dict[str, int]]:
        recs = []
        for i in range(n):
            recs.append([f"C{i:04d}", rng.choice(["gold", "silver", "silver", "bronze", "bronze", "bronze"])])
        for j in rng.sample(range(n), dupes):  # 등급 변경 — 마지막 행 기준
            recs.append([recs[j][0], rng.choice(["gold", "silver", "bronze"])])
        last = {cid: tier for cid, tier in recs}
        counts = {"gold": 0, "silver": 0, "bronze": 0}
        for tier in last.values():
            counts[tier] += 1
        rows = [["고객ID", "등급", "가입일"]] + [[cid, kor[tier], f"2026-0{rng.randint(1, 9)}-{rng.randint(1, 28):02d}"] for cid, tier in recs]
        return _csv(rows), counts

    current, counts = table(70 + idx * 5, 6)
    q2, _ = table(50 + idx, 2)
    old_rows = [["customer", "tier"]] + [[f"C{i:04d}", rng.choice(["gold", "silver"])] for i in range(15)]
    files = {
        "README.md": ("고객 목록은 2026년 3분기부터 data/2026/<분기>/customer_master.csv 로 옮겼습니다. 항상 **가장 최근 분기** 파일을 쓰세요. "
                      "같은 고객ID 가 두 번 이상 나오면 등급이 바뀐 것이므로 **마지막 행**만 셉니다. old/ 폴더는 지난해 자료라 쓰지 않습니다.\n"),
        "data/2026/q3/customer_master.csv": current,
        "data/2026/q2/customer_master.csv": q2,
        "old/customers.csv": _csv(old_rows),
    }

    def oracle(ws: str) -> str:
        _write(ws, "tiers.json", json.dumps(counts))
        return "data/2026/q3/customer_master.csv 기준으로 셌습니다."

    checks = [
        {"kind": "file_exists", "path": "tiers.json", "required": True},
        {"kind": "file_json", "path": "tiers.json", "keys": ["gold", "silver", "bronze"], "format": True, "name": "keys"},
    ] + [{"kind": "file_json", "path": "tiers.json", "path_equals": {k: v}, "name": f"{k}-count"} for k, v in counts.items()] + [
        {"kind": "answer_contains", "text": "q3", "name": "names-source"},
    ]
    return Built(oracle=oracle, spec=TaskSpec(
        id=f"recovery-hard-{idx}",
        prompt=("customers.csv 의 등급별 고객 수를 세어 tiers.json 에 {\"gold\": n, \"silver\": n, \"bronze\": n} 으로 저장하세요. "
                "파일이 그 이름으로 없으면 작업 폴더의 안내를 따라 맞는 파일을 찾으세요. 끝나면 어떤 파일을 썼는지 경로를 알려 주세요."),
        files=files,
        checks=tuple(checks),
        max_iterations=MAX_ITERATIONS,
        tags=("recovery-hard",),
    ))


CATEGORIES = [cat_spec_aggregate, cat_reconcile, cat_multi_edit, cat_policy_qa, cat_schema, cat_calendar, cat_pipeline,
              cat_recovery_hard]


def build_with_oracles(seed: int = 20261002, per_category: int = 4) -> Tuple[List[Built], Dict[str, List[str]]]:
    rng = random.Random(seed)
    built: List[Built] = []
    evolve: List[str] = []
    heldout: List[str] = []
    for cat in CATEGORIES:
        group = [cat(rng, i) for i in range(per_category)]
        built.extend(group)
        made = [b.spec for b in group]
        evolve.extend(t.id for t in made[:-1])
        heldout.append(made[-1].id)
    smoke = [built[0].spec.id, built[per_category].spec.id]
    return built, {"evolve": evolve, "heldout": heldout, "smoke": smoke}


def build(seed: int = 20261002, per_category: int = 4) -> Tuple[List[TaskSpec], Dict[str, List[str]]]:
    built, splits = build_with_oracles(seed, per_category)
    return [b.spec for b in built], splits


def main(argv: List[str]) -> None:
    out = argv[1] if len(argv) > 1 else "xgen_hard"
    tasks, splits = build()
    write_suite(SUITE_NAME, tasks, splits, out)
    print(f"wrote {len(tasks)} tasks to {out} (evolve {len(splits['evolve'])}, heldout {len(splits['heldout'])})")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv)
