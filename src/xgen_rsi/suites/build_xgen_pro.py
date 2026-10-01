"""내장 평가 스위트 ``xgen-pro`` 생성기 — 천장 아래 모델(gpt-6-luna·claude-haiku-4-5)에서 하네스 차이를 재는 업무 과제.

``xgen-hard`` 는 최상위 모델이 0.9~1.0 을 내서(천장) 하네스 개선을 가릴 수 없었다. ``xgen-pro`` 는 같은 원칙(정답은 코드로
계산, 오라클 만점·빈 답 0.5 이하를 테스트로 고정)에 **하네스가 결과를 가르는 지점**을 더 세게 넣는다:

* **긴 문맥** — 회의록 8건(3만 자+)에서 결정·철회·담당 변경·기한 연기를 끝까지 추적, 초안은 무시.
* **여러 파일 대조와 계산** — 입금·청구서·환율표를 맞춰 완납·부분·초과·미수 판정(참조 표기 정규화, 원 단위 반올림).
* **긴 정확한 출력** — 연락처 100행 정규화·중복 제거·정렬을 CSV 한 파일로.
* **여러 파일 일관 편집** — 서비스 설정 10개를 이관 규칙대로 고치고 동결 서비스는 건드리지 않기.
* **겹친 규정** — 기본 규정 + 개정 5건(시행일·적용 범위·철회) + 개인 예외 메모.
* **제약 퍼즐** — 정답이 하나뿐인 회의 배치(참석자 겹침 금지 포함).
* **도구로 찾기** — 옮겨진 문서를 내용으로 찾고 최신 개정판을 고르기.
* **검증** — 동료 보고서에서 틀린 항목만 정확히 찾아 고치기(맞는 항목을 건드리면 감점).

실행: ``rsi suite build OUT --suite xgen-pro``. 분할은 다른 스위트와 같다 — 범주마다 앞 3개 evolve, 마지막 1개 heldout, smoke 2개.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import sys
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Tuple

from xgen_rsi.evolve.tasks import TaskSpec, write_suite
from xgen_rsi.suites.build_xgen_core import Built, _csv, _sha, _write

SUITE_NAME = "xgen-pro"
MAX_ITERATIONS = 40

PEOPLE = ["김하늘", "이도윤", "박서준", "최지우", "정민재", "강수아", "조하린", "윤태오", "장예린", "임시우", "한유진", "오세훈"]


def _won_round(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# ── P1: 긴 회의록 종합 — 결정·철회·담당 변경·기한 연기 ────────────────────────

_SUBJECTS = [
    "신규 대리점 계약 조건 표준화", "분기 마감 일정 앞당기기", "고객 문의 응답 SLA 조정", "사내 위키 이전",
    "재고 실사 주기 변경", "협력사 정산 자동화", "보안 교육 의무화", "출장비 정산 양식 개편", "데이터 백업 정책 강화",
    "신입 온보딩 프로그램 개편", "회의실 예약 규칙 변경", "외주 개발 검수 절차", "가격 인상 공지 시점", "물류 창고 이전 검토",
    "마케팅 예산 재배분", "고객 만족도 조사 도입", "콜센터 운영 시간 연장", "전자 결재 단계 축소", "사내 메신저 교체",
    "법인카드 한도 재조정", "제품 반품 정책 개정", "협력사 평가 기준 신설", "개인정보 보관 기간 단축", "재택근무 신청 절차 간소화",
    "견적서 양식 통일", "부서 간 인력 지원 규칙", "장비 대여 절차 정비", "연말 행사 준비", "사내 교육 이수 관리", "외부 감사 대응 계획",
]
_FILLER = [
    "{p}: {t} 관련해서 지난주 공유한 자료를 다시 보면 수치가 조금 다릅니다. 확인해 보겠습니다.",
    "{p}: {t} 는 다른 팀 일정과 겹치니 먼저 조율이 필요합니다.",
    "{p}: 고객사 쪽 의견을 들어 보니 {t} 에 대한 반응은 나쁘지 않았습니다.",
    "{p}: {t} 진행 상황은 순조롭습니다. 특별한 이슈는 없습니다.",
    "{p}: 예산 측면에서 {t} 는 이번 분기 안에 처리할 수 있을 것 같습니다.",
    "{p}: {t} 와 관련된 문서는 공유 폴더에 올려 두었습니다.",
    "{p}: 다음 회의 전까지 {t} 초안을 한 번 더 다듬겠습니다.",
    "{p}: 법무 검토가 필요할 수 있어 {t} 는 조금 더 지켜보자는 의견입니다.",
]


def _md(date: dt.date, style: int) -> str:
    return [f"{date.month}/{date.day}", f"{date.month}월 {date.day}일", date.isoformat()][style % 3]


def cat_minutes(rng: random.Random, idx: int) -> Built:
    teams = ["영업", "개발", "운영", "재무"]
    n_meet = 20
    dates = sorted(rng.sample(range(1, 30), n_meet))
    meetings: List[Tuple[dt.date, str, List[str]]] = [(dt.date(2026, 9, d), rng.choice(teams), []) for d in dates]
    n_dec = 22 + idx
    subjects = rng.sample(_SUBJECTS, n_dec)
    state: Dict[str, Dict[str, Any]] = {}
    for k in range(n_dec):
        did = f"D-{k + 1:02d}"
        mi = rng.randint(0, 13)
        owner = rng.choice(PEOPLE)
        due = dt.date(2026, 10, rng.randint(5, 28))
        state[did] = {"subject": subjects[k], "owner": owner, "due": due, "active": True, "made": mi}
        meetings[mi][2].append(f"**[결정 {did}]** {subjects[k]} — 담당 {owner}, 기한 {_md(due, rng.randint(0, 2))}")
    # 이후 회의의 변경 — 결정마다 회의당 하나, 철회 뒤에는 더 바꾸지 않는다(해석이 갈리지 않게).
    ids = list(state)
    errata: List[Tuple[dt.date, str, str, str]] = []
    final: Dict[str, Dict[str, Any]] = {}
    for did, s in state.items():
        cur = {"owner": s["owner"], "due": s["due"], "active": True}
        later = sorted(rng.sample(range(s["made"] + 1, n_meet), k=min(rng.randint(0, 5), n_meet - 1 - s["made"])))
        for mi in later:
            kind = rng.choice(["withdraw", "owner", "due", "due", "owner", "note"])
            if kind == "withdraw":
                line = rng.choice([f"{did} 건은 철회하기로 했습니다.", f"[{did} 철회] 상황이 바뀌어 진행하지 않습니다.",
                                   f"{did} 는 없던 일로 하기로 합의(철회)."])
                cur["active"] = False
            elif kind == "owner":
                new = rng.choice([p for p in PEOPLE if p != cur["owner"]])
                line = f"{did} 담당자 변경: {new}"
                cur["owner"] = new
                # 정정 대상 후보 — 회의록에 적힌 이름이 오기였다(정정 문서가 바른 이름을 준다)
                if len(errata) < 3 and rng.random() < 0.5:
                    wrong = rng.choice([p for p in PEOPLE if p not in (new, cur["owner"])])
                    line = f"{did} 담당자 변경: {wrong}"
                    errata.append((meetings[mi][0], did, wrong, new))
            elif kind == "due":
                new_due = (dt.date(2026, 10, rng.randint(5, 31)) if rng.random() < 0.8
                           else dt.date(2026, 11, rng.randint(1, 20)))
                line = rng.choice([f"{did} 기한을 {_md(new_due, 0)} 로 조정합니다.", f"[{did} 기한 변경] {_md(new_due, 1)}",
                                   f"{did} 는 {_md(new_due, 2)} 까지로 연기."])
                cur["due"] = new_due
            else:
                line = f"{did} 진행 상황 공유: 일정대로 진행 중(변경 없음)."
            meetings[mi][2].append(line)
            if not cur["active"]:
                break
        final[did] = cur
    files: Dict[str, str] = {}
    for date, team, lines in meetings:
        body: List[str] = [f"# {date.isoformat()} {team} 주간 회의", "", f"참석: {', '.join(rng.sample(PEOPLE, 5))}", ""]
        topics = rng.sample(_SUBJECTS, 4)
        fill = [rng.choice(_FILLER).format(p=rng.choice(PEOPLE), t=rng.choice(topics)) for _ in range(70)]
        for ln in lines:
            fill.insert(rng.randint(0, len(fill)), ln)
        body += ["## 논의", ""] + [f"- {x}" for x in fill] + ["", "## 다음 회의", "", "- 다음 주 같은 시간"]
        files[f"meetings/{date.isoformat()}_{team}.md"] = "\n".join(body) + "\n"
    # 초안(무시해야 함) — 공식 회의록과 다른 내용
    for j in range(2):
        did = rng.choice(ids)
        files[f"drafts/draft_{j + 1}.md"] = (
            f"# (초안) 회의 메모\n\n이 문서는 초안입니다.\n\n- {did} 철회 검토 중\n- [{rng.choice(ids)} 담당 변경] → {rng.choice(PEOPLE)}\n"
        )
    if errata:
        files["meetings/ERRATA.md"] = "# 회의록 정정\n\n" + "\n".join(
            f"- {d.isoformat()} 회의록의 '{did} 담당자 변경: {w}' 는 오기다. 바른 담당자는 {r} 이다." for d, did, w, r in errata) + "\n"
    files["README.md"] = (
        "# 회의록 정리 규칙\n\n"
        "- 공식 회의록은 `meetings/` 폴더의 파일뿐이다. `drafts/` 는 초안이라 **반영하지 않는다**.\n"
        "- `meetings/ERRATA.md` 는 회의록의 오기를 바로잡는다. 정정된 줄은 바른 내용이 처음부터 적혀 있던 것으로 본다.\n"
        "- 결정은 `[결정 D-xx]` 줄에서 생긴다(담당·기한 포함).\n"
        "- 이후 회의에서 같은 번호의 결정이 철회되거나, 담당이 바뀌거나, 기한이 바뀔 수 있다. 회의 날짜 순으로 적용하고,\n"
        "  같은 회의 안에서는 위에서 아래 순서로 적용한다. 나중 것이 이긴다.\n"
        "- '진행 상황 공유' 처럼 바꾸는 말이 없는 줄은 아무것도 바꾸지 않는다.\n"
        "- 날짜는 `10/15`, `10월 15일`, `2026-10-15` 처럼 섞여 있다. 모두 2026년이다.\n"
    )
    active = sorted(d for d, v in final.items() if v["active"])
    withdrawn = sorted(d for d, v in final.items() if not v["active"])
    out = {"active": [{"id": d, "owner": final[d]["owner"], "due": final[d]["due"].isoformat()} for d in active],
           "withdrawn": withdrawn}

    def oracle(ws: str) -> str:
        _write(ws, "decisions.json", json.dumps(out, ensure_ascii=False))
        return "decisions.json 저장"

    checks: List[Dict[str, Any]] = [
        {"kind": "file_exists", "path": "decisions.json", "required": True},
        {"kind": "file_json", "path": "decisions.json", "keys": ["active", "withdrawn"], "format": True, "name": "shape"},
        {"kind": "file_json", "path": "decisions.json", "lengths": {"active": len(active)}, "name": "active-count"},
        {"kind": "file_json", "path": "decisions.json", "path_equals": {"withdrawn": withdrawn}, "name": "withdrawn"},
    ]
    for i, d in enumerate(active):
        checks.append({"kind": "file_json", "path": "decisions.json",
                       "path_equals": {f"active.{i}.id": d, f"active.{i}.owner": final[d]["owner"],
                                       f"active.{i}.due": final[d]["due"].isoformat()}, "name": f"{d}"})
    return Built(oracle=oracle, spec=TaskSpec(
        id=f"minutes-{idx}",
        prompt=(
            "README.md 의 규칙대로 회의록을 정리해 decisions.json 으로 저장하세요. 형식: "
            '{"active": [{"id": "D-01", "owner": 담당자, "due": "YYYY-MM-DD"}, ...], "withdrawn": ["D-xx", ...]}. '
            "active 는 지금 유효한 결정만 id 오름차순으로, withdrawn 은 철회된 결정 id 오름차순으로 적으세요."
        ),
        files=files, checks=tuple(checks), max_iterations=MAX_ITERATIONS, tags=("minutes",),
    ))


# ── P2: 장부 마감 — 입금·청구서·환율 대조 ──────────────────────────────────


def _ref_variants(inv: str, rng: random.Random) -> str:
    num = inv.split("-")[1]
    return rng.choice([inv, f"inv{num}", f"INV {num}", f"Inv-{num} ", f" inv-{num}", f"INV{num}"])


def cat_ledger(rng: random.Random, idx: int) -> Built:
    days = [dt.date(2026, 9, d) for d in range(1, 31)]
    fx = {d: (Decimal(1350 + rng.randint(-25, 25)) + Decimal(rng.randint(0, 9)) / 10,
              Decimal(905 + rng.randint(-15, 15)) + Decimal(rng.randint(0, 99)) / 100) for d in days}
    customers = ["한빛상사", "대성무역", "누리유통", "미래물산", "새벽식품", "초록에너지", "바른제약", "온새미로"]
    n_inv = 24 + idx * 2
    invoices = []
    payments: List[List[Any]] = []
    status: Dict[str, str] = {}
    paid_sum: Dict[str, int] = {}
    for i in range(n_inv):
        inv = f"INV-{100 + i:04d}"
        kind = rng.choice(["paid", "paid", "paid", "partial", "over", "unpaid"])
        parts = rng.randint(1, 3) if kind != "unpaid" else 0
        krw_parts: List[int] = []
        for _ in range(parts):
            d = rng.choice(days)
            cur = rng.choice(["KRW", "KRW", "KRW", "USD", "JPY"])
            if cur == "KRW":
                amt = rng.randint(30, 900) * 1000
                krw = amt
                amt_txt = f"{amt}"
            elif cur == "USD":
                amt = Decimal(rng.randint(100, 3000)) + Decimal(rng.randint(0, 99)) / 100
                krw = _won_round(amt * fx[d][0])
                amt_txt = f"{amt:.2f}"
            else:
                amt = Decimal(rng.randint(10, 300) * 1000)
                krw = _won_round(amt / 100 * fx[d][1])
                amt_txt = f"{int(amt)}"
            krw_parts.append(krw)
            payments.append([d.isoformat(), _ref_variants(inv, rng), amt_txt, cur])
        total = sum(krw_parts)
        if kind == "paid":
            amount = total
        elif kind == "partial":
            amount = total + rng.randint(5, 80) * 1000
        elif kind == "over":
            amount = max(1000, total - rng.randint(1, 40) * 1000)
        else:
            amount = rng.randint(50, 600) * 1000
        if kind == "over" and amount >= total:
            kind = "paid"
            amount = total
        invoices.append([inv, rng.choice(customers), amount])
        status[inv] = kind
        paid_sum[inv] = total
    unmatched = []
    for _ in range(3 + idx % 2):
        bad = rng.choice([f"INV-{900 + rng.randint(0, 99):04d}", f"주문{rng.randint(1000, 9999)}", f"PO-{rng.randint(10, 99)}"])
        d = rng.choice(days)
        payments.append([d.isoformat(), bad, f"{rng.randint(10, 300) * 1000}", "KRW"])
        unmatched.append(bad)
    rng.shuffle(payments)
    partial = sorted(i for i, k in status.items() if k == "partial")
    over = sorted(i for i, k in status.items() if k == "over")
    paid = sum(1 for k in status.values() if k == "paid")
    amounts = {r[0]: r[2] for r in invoices}
    outstanding = sum(amounts[i] - paid_sum[i] for i in partial) + sum(amounts[i] for i, k in status.items() if k == "unpaid")
    rules = (
        "# 9월 마감 규칙\n\n"
        "1. payments.csv 의 ref 는 청구서 번호를 여러 표기로 적었다(`inv0101`, `INV 0101`, `Inv-0101`, 앞뒤 공백 등). 대소문자·공백·하이픈을\n"
        "   무시하고 `INV-0101` 꼴로 맞춘다. 맞춘 번호가 invoices.csv 에 없으면 **미확인 입금**이다.\n"
        "2. 원화가 아닌 입금은 **입금일의** fx.csv 환율로 원화로 바꾼다. USD 는 `금액 × USD`, JPY 는 `금액 ÷ 100 × JPY100`.\n"
        "   바꾼 값은 입금 한 건마다 **원 단위로 반올림**(0.5 는 올림)한다.\n"
        "3. 청구서별 입금 합계가 청구 금액과 같으면 완납, 작으면 부분 입금, 크면 초과 입금, 입금이 없으면 미입금이다.\n"
        "4. 미수금 = 부분 입금 청구서의 (청구 금액 − 입금 합계) + 미입금 청구서의 청구 금액.\n"
    )
    out = {"paid": paid, "partial": partial, "overpaid": over, "outstanding_krw": outstanding,
           "unmatched": sorted(r.strip() for r in unmatched)}

    def oracle(ws: str) -> str:
        _write(ws, "close.json", json.dumps(out, ensure_ascii=False))
        return "close.json 저장"

    fx_rows = [["date", "USD", "JPY100"]] + [[d.isoformat(), f"{fx[d][0]:.1f}", f"{fx[d][1]:.2f}"] for d in days]
    pay_rows = [["date", "ref", "amount", "currency"]] + payments
    inv_rows = [["invoice_id", "customer", "amount_krw"]] + invoices
    checks: List[Dict[str, Any]] = [
        {"kind": "file_exists", "path": "close.json", "required": True},
        {"kind": "file_json", "path": "close.json", "keys": ["paid", "partial", "overpaid", "outstanding_krw", "unmatched"],
         "format": True, "name": "shape"},
        {"kind": "file_json", "path": "close.json", "path_equals": {"paid": paid}, "name": "paid-count"},
        {"kind": "file_json", "path": "close.json", "path_equals": {"partial": partial}, "name": "partial"},
        {"kind": "file_json", "path": "close.json", "path_equals": {"overpaid": over}, "name": "overpaid"},
        {"kind": "file_json", "path": "close.json", "path_equals": {"outstanding_krw": outstanding}, "name": "outstanding"},
        {"kind": "file_json", "path": "close.json", "path_equals": {"unmatched": out["unmatched"]}, "name": "unmatched"},
        {"kind": "file_unchanged", "path": "payments.csv", "sha256": _sha(_csv(pay_rows)), "name": "input-preserved"},
    ]
    return Built(oracle=oracle, spec=TaskSpec(
        id=f"ledger-{idx}",
        prompt=(
            "RULES.md 대로 9월 입금을 청구서와 맞춰 close.json 으로 저장하세요. 형식: "
            '{"paid": 완납 청구서 수, "partial": [부분 입금 청구서 번호, 오름차순], "overpaid": [초과 입금 청구서 번호, 오름차순], '
            '"outstanding_krw": 미수금(정수), "unmatched": [미확인 입금의 ref 원문(앞뒤 공백 제거), 오름차순]}. 입력 파일은 고치지 마세요.'
        ),
        files={"payments.csv": _csv(pay_rows), "invoices.csv": _csv(inv_rows), "fx.csv": _csv(fx_rows), "RULES.md": rules},
        checks=tuple(checks), max_iterations=MAX_ITERATIONS, tags=("ledger",),
    ))


# ── P3: 대량 변환 — 연락처 정규화·중복 제거·정렬(긴 정확한 출력) ─────────────

_DEPTS = {"영업": ["영업", "영업팀", "Sales", "sales"], "개발": ["개발", "개발팀", "Dev", "R&D"],
          "재무": ["재무", "재무팀", "Finance"], "인사": ["인사", "인사팀", "HR"]}
_SURN = "김이박최정강조윤장임한오서신권황안송류홍"
_GIVEN = ["민준", "서연", "도윤", "하은", "시우", "지유", "예준", "수아", "주원", "지아", "하준", "서윤", "건우", "채원", "우진", "다은"]


def cat_contacts(rng: random.Random, idx: int) -> Built:
    n = 80 + idx * 6
    people = []
    used = set()
    while len(people) < n:
        name = rng.choice(_SURN) + rng.choice(_GIVEN)
        local = f"user{rng.randint(100, 999)}"
        if local in used:
            continue
        used.add(local)
        people.append({"name": name, "local": local, "dept": rng.choice(list(_DEPTS)),
                       "phone": f"010-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}"})
    rows: List[List[Any]] = []
    base = dt.datetime(2026, 9, 1, 9, 0)
    for p in people:
        for _ in range(rng.choice([1, 1, 1, 2])):  # 같은 사람 여러 행(중복)
            ph = p["phone"].split("-")
            phone = rng.choice([p["phone"], f"010 {ph[1]} {ph[2]}", f"010{ph[1]}{ph[2]}", f"+82-10-{ph[1]}-{ph[2]}",
                                f"(010){ph[1]}-{ph[2]}"])
            email = rng.choice([f"{p['local']}@corp.example", f"{p['local'].upper()}@CORP.EXAMPLE", f" {p['local']}@corp.example "])
            name = rng.choice([p["name"], f" {p['name']}", f"{p['name'][0]} {p['name'][1:]}"])
            dept = rng.choice(_DEPTS[p["dept"]])
            ts = base + dt.timedelta(minutes=rng.randint(0, 40000))
            rows.append([name, phone, email, dept, ts.strftime("%Y-%m-%d %H:%M")])
    for _ in range(4 + idx):  # 잘못된 이메일 — 버린다
        rows.append([rng.choice(_SURN) + rng.choice(_GIVEN), "010-0000-0000",
                     rng.choice(["nobody@", "user000corp.example", "user001@corp", ""]), rng.choice(list(_DEPTS)), "2026-09-02 10:00"])
    rng.shuffle(rows)
    # 정답: 이메일 유효성 → 같은 이메일 중 updated_at 가 가장 늦은 행(같으면 아래쪽) → 정규화 → 부서, 이름 순 정렬
    import re as _re

    valid = _re.compile(r"^[a-z0-9._-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")
    best: Dict[str, Tuple[str, int, List[Any]]] = {}
    for pos, r in enumerate(rows):
        email = r[2].strip().lower()
        if not valid.match(email):
            continue
        cur = best.get(email)
        if cur is None or (r[4], pos) > (cur[0], cur[1]):
            best[email] = (r[4], pos, r)
    canon = {a: k for k, vs in _DEPTS.items() for a in vs}
    out_rows = []
    for email, (_, _, r) in best.items():
        digits = _re.sub(r"\D", "", r[1])
        if digits.startswith("8210"):
            digits = "0" + digits[2:]
        phone = f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
        out_rows.append([r[0].replace(" ", ""), phone, email, canon[r[3].strip()]])
    out_rows.sort(key=lambda x: (x[3], x[0], x[2]))
    header = ["name", "phone", "email", "dept"]
    rules = (
        "# 연락처 정리 규칙\n\n"
        "1. email 은 앞뒤 공백을 지우고 소문자로 바꾼다. `영문소문자·숫자·.·_·-` + `@` + 점이 하나 이상 있는 도메인 꼴이 아니면 그 행을 버린다.\n"
        "2. 같은 email 이 여러 행이면 updated_at 이 가장 늦은 행 하나만 남긴다(같으면 파일에서 아래쪽 행).\n"
        "3. name 은 모든 공백을 지운다. phone 은 숫자만 남겨 `010-XXXX-XXXX` 로 쓴다(`+82-10-…` 은 `010-…`).\n"
        "4. dept 는 '영업'·'개발'·'재무'·'인사' 중 하나로 맞춘다(Sales→영업, Dev·R&D→개발, Finance→재무, HR→인사, '…팀' 은 팀을 뗀다).\n"
        "5. 결과는 dept, name, email 순으로 오름차순 정렬한다(문자열 비교).\n"
    )
    raw = _csv([["name", "phone", "email", "dept", "updated_at"]] + rows)

    def oracle(ws: str) -> str:
        _write(ws, "contacts.csv", _csv([header] + out_rows))
        return "contacts.csv 저장"

    picks = sorted(set([0, len(out_rows) - 1] + rng.sample(range(len(out_rows)), 8)))
    checks: List[Dict[str, Any]] = [
        {"kind": "file_exists", "path": "contacts.csv", "required": True},
        {"kind": "file_csv", "path": "contacts.csv", "header": header, "format": True, "name": "header"},
        {"kind": "file_csv", "path": "contacts.csv", "rows": len(out_rows), "name": "row-count"},
        {"kind": "file_csv", "path": "contacts.csv", "unique": "email", "name": "unique-email"},
        {"kind": "file_csv", "path": "contacts.csv", "allowed": {"dept": list(_DEPTS)}, "name": "dept-values"},
    ]
    for k in picks:
        checks.append({"kind": "file_csv", "path": "contacts.csv",
                       "cells": [{"row": k, "col": c, "value": out_rows[k][j]} for j, c in enumerate(header)], "name": f"row-{k}"})
    checks.append({"kind": "file_unchanged", "path": "contacts_raw.csv", "sha256": _sha(raw), "name": "input-preserved"})
    return Built(oracle=oracle, spec=TaskSpec(
        id=f"contacts-{idx}",
        prompt=("RULES.md 대로 contacts_raw.csv 를 정리해 contacts.csv 로 저장하세요(헤더 name,phone,email,dept). "
                "contacts_raw.csv 는 고치지 마세요."),
        files={"contacts_raw.csv": raw, "RULES.md": rules},
        checks=tuple(checks), max_iterations=MAX_ITERATIONS, tags=("contacts",),
    ))


# ── P4: 설정 이관 — 서비스 10개를 규칙대로, 동결 서비스는 그대로 ────────────────

_LEVELS = {"debug": "DEBUG", "info": "INFO", "warn": "WARNING", "warning": "WARNING", "error": "ERROR"}


def cat_config(rng: random.Random, idx: int) -> Built:
    names = rng.sample(["auth", "billing", "catalog", "search", "notify", "report", "gateway", "ledger", "media", "audit",
                        "profile", "cart", "pricing", "inventory", "shipping", "review", "coupon", "loyalty", "payment",
                        "invoice", "tax", "fraud", "chat", "export", "import", "scheduler"], 24)
    frozen = set(rng.sample(names, 4))
    movable = [n for n in names if n not in frozen]
    tier1 = sorted(rng.sample(movable, 8))
    port_moves = {n: 9000 + rng.randint(100, 899) for n in rng.sample(movable, 5)}
    files: Dict[str, str] = {}
    checks: List[Dict[str, Any]] = []
    plan: Dict[str, Dict[str, Any]] = {}
    for name in names:
        timeout = rng.choice([5, 10, 15, 30, 45, 60])
        db_timeout = rng.choice([3, 5, 8, 20])
        level = rng.choice(list(_LEVELS))
        replicas = rng.randint(1, 5)
        port = 8000 + rng.randint(1, 999)
        legacy = rng.random() < 0.5
        server = [f"port = {port}", f"timeout = {timeout}", f"replicas = {replicas}"]
        if legacy:
            server.insert(rng.randint(0, len(server)), "legacy_mode = true")
        lines = [f"# {name} 서비스 설정 — 운영팀 관리", "[server]"] + server + ["", "[db]", f"host = db-{name}.internal",
                 f"timeout = {db_timeout}", "", "[logging]", f"# 마지막 점검: 2026-0{rng.randint(6, 9)}-{rng.randint(10, 28)}",
                 f"log_level = {level}"]
        if name in frozen:
            lines += ["", "[meta]", "frozen = true"]
        text = "\n".join(lines) + "\n"
        path = f"services/{name}/app.ini"
        files[path] = text
        plan[name] = {"timeout": timeout, "db_timeout": db_timeout, "level": level, "replicas": replicas, "port": port}
        if name in frozen:
            checks.append({"kind": "file_unchanged", "path": path, "sha256": _sha(text), "name": f"{name}-frozen"})
            continue
        checks += [
            {"kind": "file_contains", "path": path, "regex": rf"^timeout_ms = {timeout * 1000}$", "name": f"{name}-server-timeout"},
            {"kind": "file_contains", "path": path, "regex": rf"^\[db\]\nhost = db-{name}\.internal\ntimeout = {db_timeout}$",
             "name": f"{name}-db-timeout-kept"},
            {"kind": "file_contains", "path": path, "regex": rf"^log_level = {_LEVELS[level]}$", "name": f"{name}-log-level"},
            {"kind": "file_contains", "path": path, "regex": rf"^# {name} 서비스 설정", "name": f"{name}-comment-kept"},
        ]
        if legacy:
            checks.append({"kind": "file_not_contains", "path": path, "regex": r"legacy_mode", "name": f"{name}-legacy-removed"})
        if name in tier1:
            checks.append({"kind": "file_contains", "path": path, "regex": rf"^replicas = {max(replicas, 3)}$", "name": f"{name}-replicas"})
        if name in port_moves:
            checks.append({"kind": "file_contains", "path": path, "regex": rf"^port = {port_moves[name]}$", "name": f"{name}-port"})
    files["tier1.txt"] = "\n".join(tier1) + "\n"
    files["ports.csv"] = _csv([["service", "new_port"]] + [[n, p] for n, p in sorted(port_moves.items())]
                              + [[n, 9999] for n in sorted(frozen)[:1]])
    files["MIGRATION.md"] = (
        "# 설정 이관 v2\n\n"
        "`services/*/app.ini` 를 모두 아래 규칙대로 고친다. 단 `[meta]` 에 `frozen = true` 가 있는 서비스는 **한 글자도 고치지 않는다**\n"
        "(ports.csv 에 있어도 그대로다).\n\n"
        "1. `[server]` 섹션의 `timeout = N`(초) 줄만 `timeout_ms = N×1000` 줄로 바꾼다. **`[db]` 의 timeout 은 그대로 둔다.**\n"
        "2. `log_level` 값은 대문자 표준값으로: debug→DEBUG, info→INFO, warn·warning→WARNING, error→ERROR.\n"
        "3. `legacy_mode` 줄은 지운다.\n"
        "4. `tier1.txt` 에 있는 서비스는 `replicas` 를 3 이상으로(3보다 작으면 3, 크거나 같으면 그대로).\n"
        "5. `ports.csv` 에 있는 서비스는 `port` 를 new_port 로 바꾼다.\n"
        "6. 주석(`#` 줄)·빈 줄·섹션 순서·그 밖의 줄은 그대로 둔다.\n"
    )

    def oracle(ws: str) -> str:
        for name in names:
            if name in frozen:
                continue
            path = f"services/{name}/app.ini"
            out, section = [], ""
            for ln in files[path].splitlines():
                if ln.startswith("["):
                    section = ln
                key, _, val = ln.partition(" = ")
                if section == "[server]" and key == "timeout":
                    out.append(f"timeout_ms = {int(val) * 1000}")
                elif key == "log_level":
                    out.append(f"log_level = {_LEVELS[val]}")
                elif key == "legacy_mode":
                    continue
                elif section == "[server]" and key == "replicas" and name in tier1:
                    out.append(f"replicas = {max(int(val), 3)}")
                elif section == "[server]" and key == "port" and name in port_moves:
                    out.append(f"port = {port_moves[name]}")
                else:
                    out.append(ln)
            _write(ws, path, "\n".join(out) + "\n")
        return "이관 완료"

    return Built(oracle=oracle, spec=TaskSpec(
        id=f"config-{idx}",
        prompt="MIGRATION.md 대로 services/ 아래 설정 파일을 모두 이관하세요(해당 파일을 직접 고칩니다).",
        files=files, checks=tuple(checks), max_iterations=MAX_ITERATIONS, tags=("config",),
    ))


# ── P5: 겹친 규정 — 기본 규정 + 개정(시행일·범위·철회) + 개인 예외 ─────────────

_GRADES = ["사원", "대리", "과장", "차장", "부장"]
_DEPTS5 = ["영업본부", "개발본부", "경영지원"]


def cat_policy(rng: random.Random, idx: int) -> Built:
    # 항목: hotel(1박 숙박 한도, 직급별), per_diem(일비), ktx_first(특실 허용 직급 하한), approval(사전 승인 기준 금액)
    base = {"hotel": {g: v for g, v in zip(_GRADES, [90000, 100000, 120000, 140000, 170000])},
            "per_diem": 30000, "ktx_first_min": "부장", "approval": 500000}
    amendments: List[Dict[str, Any]] = []
    start = dt.date(2026, 7, 1)
    for k in range(12):
        eff = start + dt.timedelta(days=rng.randint(0, 120))
        scope = rng.choice([None, None, rng.choice(_DEPTS5)])
        min_grade = rng.choice([None, None, None, "과장"])
        item = rng.choice(["hotel", "per_diem", "ktx_first_min", "approval"])
        if item == "hotel":
            grade = rng.choice(_GRADES[_GRADES.index(min_grade):] if min_grade else _GRADES)
            value: Any = {grade: base["hotel"][grade] + rng.choice([10000, 20000, 30000, -10000])}
        elif item == "per_diem":
            value = rng.choice([35000, 40000, 25000])
        elif item == "ktx_first_min":
            value = rng.choice(["과장", "차장"])
        else:
            value = rng.choice([300000, 700000, 1000000])
        amendments.append({"no": k + 1, "eff": eff, "scope": scope, "min_grade": min_grade, "item": item, "value": value})
    # 철회 둘 — 나중 개정이 앞 개정을 철회(철회 시행일부터 그 개정은 없던 것)
    for no, w in zip((13, 14, 15), rng.sample(range(0, 12), 3)):
        withdraw_eff = amendments[w]["eff"] + dt.timedelta(days=rng.randint(5, 40))
        amendments.append({"no": no, "eff": withdraw_eff, "scope": None, "min_grade": None, "item": "withdraw",
                           "value": amendments[w]["no"]})
    names = rng.sample(PEOPLE, 5)
    memos = [{"name": names[0], "item": "hotel", "value": 200000, "from": dt.date(2026, 8, 1), "until": dt.date(2026, 12, 31)},
             {"name": names[1], "item": "ktx_first", "value": True, "from": dt.date(2026, 7, 15), "until": dt.date(2026, 9, 30)},
             {"name": names[2], "item": "hotel", "value": 200000, "from": dt.date(2026, 9, 10), "until": dt.date(2026, 10, 31)}]

    def rule(date: dt.date, dept: str, grade: str, name: str, item: str) -> Any:
        hotel = dict(base["hotel"])
        per_diem, ktx_min, approval = base["per_diem"], base["ktx_first_min"], base["approval"]
        withdrawn = {a["value"] for a in amendments if a["item"] == "withdraw" and a["eff"] <= date}
        for a in sorted(amendments, key=lambda a: (a["eff"], a["no"])):
            if a["item"] == "withdraw" or a["eff"] > date or a["no"] in withdrawn:
                continue
            if a["scope"] and a["scope"] != dept:
                continue
            if a["min_grade"] and _GRADES.index(grade) < _GRADES.index(a["min_grade"]):
                continue
            if a["item"] == "hotel":
                hotel.update(a["value"])
            elif a["item"] == "per_diem":
                per_diem = a["value"]
            elif a["item"] == "ktx_first_min":
                ktx_min = a["value"]
            else:
                approval = a["value"]
        for m in memos:
            if m["name"] == name and m["from"] <= date <= m["until"]:
                if m["item"] == "hotel" and item == "hotel":
                    return m["value"]
                if m["item"] == "ktx_first" and item == "ktx_first":
                    return True
        if item == "hotel":
            return hotel[grade]
        if item == "per_diem":
            return per_diem
        if item == "ktx_first":
            return _GRADES.index(grade) >= _GRADES.index(ktx_min)
        return approval

    questions = []
    answers: Dict[str, Any] = {}
    for q in range(12):
        date = start + dt.timedelta(days=rng.randint(0, 150))
        dept, grade = rng.choice(_DEPTS5), rng.choice(_GRADES)
        name = rng.choice(names + rng.sample(PEOPLE, 2))
        item = rng.choice(["hotel", "per_diem", "ktx_first", "approval"])
        label = {"hotel": "1박 숙박 한도(원)", "per_diem": "일비(원)", "ktx_first": "KTX 특실 이용 가능 여부(true/false)",
                 "approval": "사전 승인이 필요한 출장비 기준 금액(원)"}[item]
        questions.append(f"- Q{q + 1}: {date.isoformat()} 출장, {dept} {grade} {name} — {label}")
        answers[f"Q{q + 1}"] = rule(date, dept, grade, name, item)
    files: Dict[str, str] = {
        "policy/base.md": (
            "# 출장 규정(기본, 2026-07-01 시행)\n\n"
            "1. 1박 숙박 한도: " + ", ".join(f"{g} {v:,}원" for g, v in base["hotel"].items()) + "\n"
            f"2. 일비: {base['per_diem']:,}원\n"
            f"3. KTX 특실: {base['ktx_first_min']} 이상만 이용 가능\n"
            f"4. 출장비가 {base['approval']:,}원 이상이면 사전 승인이 필요하다\n"
        ),
        "QUESTIONS.md": "# 질문\n\n" + "\n".join(questions) + "\n",
        "README.md": (
            "# 규정 해석 순서\n\n"
            "- 기본 규정(`policy/base.md`)에 개정(`policy/amend-*.md`)을 **시행일 순서**로 적용한다(같은 날이면 번호 순). 출장일보다\n"
            "  늦게 시행되는 개정은 적용하지 않는다.\n"
            "- 적용 범위가 적힌 개정은 그 본부에만, 적용 직급이 적힌 개정은 그 직급 이상에만 적용한다.\n"
            "- 철회된 개정은 **철회 시행일부터** 없던 것으로 본다(그 전 출장에는 적용된다).\n"
            "- 개인 예외(`memos/`)는 유효 기간 안에서 규정보다 우선한다.\n"
        ),
    }
    for a in amendments:
        if a["item"] == "withdraw":
            body = f"개정 {a['value']}호를 철회한다."
        elif a["item"] == "hotel":
            (g, v), = a["value"].items()
            body = f"{g} 1박 숙박 한도를 {v:,}원으로 한다."
        elif a["item"] == "per_diem":
            body = f"일비를 {a['value']:,}원으로 한다."
        elif a["item"] == "ktx_first_min":
            body = f"KTX 특실은 {a['value']} 이상 이용할 수 있다."
        else:
            body = f"사전 승인 기준 금액을 {a['value']:,}원으로 한다."
        scope = f"\n적용 범위: {a['scope']}" if a["scope"] else "\n적용 범위: 전사"
        if a.get("min_grade"):
            scope += f"\n적용 직급: {a['min_grade']} 이상"
        files[f"policy/amend-{a['no']:02d}.md"] = f"# 개정 {a['no']}호\n\n시행일: {a['eff'].isoformat()}{scope}\n\n{body}\n"
    for i, m in enumerate(memos):
        what = "1박 숙박 한도 200,000원" if m["item"] == "hotel" else "KTX 특실 이용 허용"
        files[f"memos/exception-{i + 1}.md"] = (f"# 개인 예외\n\n대상: {m['name']}\n내용: {what}\n"
                                               f"유효 기간: {m['from'].isoformat()}부터 {m['until'].isoformat()}까지\n")

    def oracle(ws: str) -> str:
        _write(ws, "answers.json", json.dumps(answers, ensure_ascii=False))
        return "answers.json 저장"

    checks: List[Dict[str, Any]] = [
        {"kind": "file_exists", "path": "answers.json", "required": True},
        {"kind": "file_json", "path": "answers.json", "keys": list(answers), "format": True, "name": "shape"},
    ] + [{"kind": "file_json", "path": "answers.json", "path_equals": {q: v}, "name": q} for q, v in answers.items()]
    return Built(oracle=oracle, spec=TaskSpec(
        id=f"policy-{idx}",
        prompt=("README.md 의 해석 순서대로 QUESTIONS.md 의 질문에 답해 answers.json 으로 저장하세요. 형식: "
                '{"Q1": 값, ...} — 금액은 정수(원), 가능 여부는 true/false.'),
        files=files, checks=tuple(checks), max_iterations=MAX_ITERATIONS, tags=("policy",),
    ))


# ── P6: 제약 퍼즐 — 정답이 하나뿐인 회의 배치 ─────────────────────────────────


def cat_schedule(rng: random.Random, idx: int) -> Built:
    rooms, slots = ["A", "B", "C"], [1, 2, 3, 4]
    meetings = [f"M{i + 1}" for i in range(10 + (idx % 2))]
    cells = [(r, s) for s in slots for r in rooms]
    target = dict(zip(meetings, rng.sample(cells, len(meetings))))
    # 참석자 — 같은 교시의 회의끼리는 겹치지 않게 교시마다 사람을 나눠 준다(정답이 규칙을 지키게).
    pool = PEOPLE[:10]
    attendees: Dict[str, List[str]] = {}
    for slot in slots:
        here = [m for m in meetings if target[m][1] == slot]
        free = rng.sample(pool, len(pool))
        for m in here:
            k = rng.choice([2, 3])
            attendees[m], free = sorted(free[:k]), free[k:]

    def fits(m: str, cell: Tuple[str, int], assign: Dict[str, Tuple[str, int]], cons: List[Tuple]) -> bool:
        for other, oc in assign.items():
            if oc == cell or (oc[1] == cell[1] and set(attendees[m]) & set(attendees[other])):
                return False
        full = {**assign, m: cell}
        for c in cons:
            kind = c[0]
            if kind == "room" and c[1] == m and cell[0] != c[2]:
                return False
            if kind == "not_room" and c[1] == m and cell[0] == c[2]:
                return False
            if kind == "not_slot" and c[1] == m and cell[1] == c[2]:
                return False
            if kind in ("before", "same_room") and c[1] in full and c[2] in full:
                a, b = full[c[1]], full[c[2]]
                if kind == "before" and not a[1] < b[1]:
                    return False
                if kind == "same_room" and a[0] != b[0]:
                    return False
        return True

    def solutions(cons: List[Tuple], limit: int = 2) -> int:
        count = 0

        def go(i: int, assign: Dict[str, Tuple[str, int]]) -> None:
            nonlocal count
            if count >= limit:
                return
            if i == len(meetings):
                count += 1
                return
            m = meetings[i]
            for cell in cells:
                if fits(m, cell, assign, cons):
                    assign[m] = cell
                    go(i + 1, assign)
                    del assign[m]

        go(0, {})
        return count

    def candidate() -> Tuple:
        a, b = rng.sample(meetings, 2)
        kind = rng.choice(["room", "not_slot", "before", "same_room", "not_room", "not_slot", "before", "before"])
        if kind == "room":
            return ("room", a, target[a][0])
        if kind == "not_slot":
            return ("not_slot", a, rng.choice([s for s in slots if s != target[a][1]]))
        if kind == "not_room":
            return ("not_room", a, rng.choice([r for r in rooms if r != target[a][0]]))
        if kind == "before" and target[a][1] != target[b][1]:
            x, y = (a, b) if target[a][1] < target[b][1] else (b, a)
            return ("before", x, y)
        if kind == "same_room" and target[a][0] == target[b][0]:
            return ("same_room", a, b)
        return ("not_slot", a, rng.choice([s for s in slots if s != target[a][1]]))

    cons: List[Tuple] = []
    while solutions(cons) > 1:
        c = candidate()
        if c not in cons:
            cons.append(c)
    # 군더더기 제거 — 빼도 정답이 하나면 뺀다(문제를 짧고 빡빡하게)
    for c in list(cons):
        trial = [x for x in cons if x != c]
        if solutions(trial) == 1:
            cons = trial
    text = {"room": lambda c: f"{c[1]} 은(는) {c[2]}실에서 한다.", "not_slot": lambda c: f"{c[1]} 은(는) {c[2]}교시가 아니다.",
            "before": lambda c: f"{c[1]} 은(는) {c[2]} 보다 이른 교시다.", "same_room": lambda c: f"{c[1]} 과 {c[2]} 는 같은 방이다.",
            "not_room": lambda c: f"{c[1]} 은(는) {c[2]}실이 아니다."}
    rng.shuffle(cons)
    puzzle = (
        "# 회의 배치\n\n"
        f"방: {', '.join(rooms)} / 교시: {', '.join(map(str, slots))}. 한 방·한 교시에는 회의 하나만 들어간다.\n\n"
        "## 참석자\n\n" + "\n".join(f"- {m}: {', '.join(attendees[m])}" for m in meetings) + "\n\n"
        "## 규칙\n\n- 한 사람이 같은 교시의 두 회의에 들어갈 수 없다.\n" + "\n".join(f"- {text[c[0]](c)}" for c in cons) + "\n\n"
        "규칙을 모두 지키는 배치는 하나뿐이다.\n"
    )
    out = {m: {"room": target[m][0], "slot": target[m][1]} for m in meetings}

    def oracle(ws: str) -> str:
        _write(ws, "schedule.json", json.dumps(out, ensure_ascii=False))
        return "schedule.json 저장"

    checks: List[Dict[str, Any]] = [
        {"kind": "file_exists", "path": "schedule.json", "required": True},
        {"kind": "file_json", "path": "schedule.json", "keys": meetings, "format": True, "name": "shape"},
    ] + [{"kind": "file_json", "path": "schedule.json", "path_equals": {f"{m}.room": target[m][0], f"{m}.slot": target[m][1]},
          "name": m} for m in meetings]
    return Built(oracle=oracle, spec=TaskSpec(
        id=f"schedule-{idx}",
        prompt=('PUZZLE.md 의 회의를 배치해 schedule.json 으로 저장하세요. 형식: {"M1": {"room": "A", "slot": 1}, ...} '
                "(slot 은 정수)."),
        files={"PUZZLE.md": puzzle}, checks=tuple(checks), max_iterations=MAX_ITERATIONS, tags=("schedule",),
    ))


# ── P7: 도구로 찾기 — 옮겨진 문서를 내용으로, 최신 개정판으로 ───────────────────


def cat_search(rng: random.Random, idx: int) -> Built:
    codes = rng.sample(["ALPHA", "BRAVO", "CEDAR", "DELTA", "EMBER", "FJORD", "GLADE", "HARBOR"], 5)
    files: Dict[str, str] = {}
    docs: Dict[str, Dict[str, Any]] = {}
    for k in range(56 + idx * 4):
        doc_id = f"DOC-{rng.randint(1000, 9999)}"
        while doc_id in docs:
            doc_id = f"DOC-{rng.randint(1000, 9999)}"
        code = rng.choice(codes)
        revs = rng.choice([1, 2, 2, 3, 4])
        versions = []
        for rv in range(1, revs + 1):
            owner, budget = rng.choice(PEOPLE), rng.randint(10, 900) * 100000
            folder = rng.choice([f"projects/{code.lower()}/docs", f"projects/{code.lower()}/archive", f"shared/{code.lower()}",
                                 f"archive/2025/{code.lower()}"])
            path = f"{folder}/{doc_id.lower()}_r{rv}_{rng.randint(10, 99)}.md"
            body = [rng.choice(_FILLER).format(p=rng.choice(PEOPLE), t=rng.choice(_SUBJECTS)) for _ in range(8)]
            if docs and rng.random() < 0.6:  # 다른 문서를 언급하는 줄 — 그 문서의 정본이 아니다(미끼)
                ref = rng.choice(sorted(docs))
                body.insert(rng.randint(0, len(body)), f"참고 문서: {ref} (담당 {rng.choice(PEOPLE)}, 예산 {rng.randint(10, 900) * 100000:,}원 기준)")
            files[path] = (f"# {code} 프로젝트 문서\n\n문서번호: {doc_id}\n개정: r{rv}\n담당: {owner}\n예산: {budget:,}원\n\n"
                           + "\n".join(body) + "\n")
            versions.append({"rev": rv, "path": path, "owner": owner, "budget": budget})
        docs[doc_id] = {"code": code, "versions": versions}
    # 임시 사본(무시) — 최신 개정보다 높은 번호를 단 초안
    targets = rng.sample(sorted(docs), 12)
    for t in rng.sample(targets, 4):
        top = max(v["rev"] for v in docs[t]["versions"])
        files[f"tmp/{t.lower()}_draft.md"] = (f"# (임시) 편집 중\n\n문서번호: {t}\n개정: r{top + 1}\n담당: {rng.choice(PEOPLE)}\n"
                                              f"예산: {rng.randint(10, 900) * 100000:,}원\n")
    index = ["# 문서 색인(2025년판 — 경로가 바뀌었을 수 있음)", ""]
    for t in targets:
        index.append(f"- {t}: old/{docs[t]['code'].lower()}/{t.lower()}.md")
    files["INDEX.md"] = "\n".join(index) + "\n"
    files["README.md"] = (
        "# 문서 찾기 규칙\n\n"
        "- INDEX.md 의 경로는 낡았다. 문서는 **본문의 `문서번호:`** 로 찾는다.\n"
        "- 같은 문서번호의 파일이 여러 개면 `개정: rN` 이 가장 큰 것이 정본이다.\n"
        "- 다른 문서 본문의 `참고 문서: DOC-…` 줄은 언급일 뿐 그 문서가 아니다(그 줄의 담당·예산도 쓰지 않는다).\n"
        "- `tmp/` 는 편집 중인 임시 사본이라 정본이 될 수 없다.\n"
    )
    out = {}
    for t in targets:
        best = max(docs[t]["versions"], key=lambda v: v["rev"])
        out[t] = {"path": best["path"], "owner": best["owner"], "budget": best["budget"]}

    def oracle(ws: str) -> str:
        _write(ws, "found.json", json.dumps(out, ensure_ascii=False))
        return "found.json 저장"

    checks: List[Dict[str, Any]] = [
        {"kind": "file_exists", "path": "found.json", "required": True},
        {"kind": "file_json", "path": "found.json", "keys": targets, "format": True, "name": "shape"},
    ] + [{"kind": "file_json", "path": "found.json",
          "path_equals": {f"{t}.path": out[t]["path"], f"{t}.owner": out[t]["owner"], f"{t}.budget": out[t]["budget"]},
          "name": t} for t in targets]
    return Built(oracle=oracle, spec=TaskSpec(
        id=f"search-{idx}",
        prompt=("INDEX.md 의 문서 12건을 README.md 규칙대로 찾아 found.json 으로 저장하세요. 형식: "
                '{"DOC-xxxx": {"path": 작업 공간 기준 상대 경로, "owner": 담당, "budget": 예산(정수, 원)}, ...}.'),
        files=files, checks=tuple(checks), max_iterations=MAX_ITERATIONS, tags=("search",),
    ))


# ── P8: 검증 — 동료 보고서에서 틀린 항목만 고치기 ─────────────────────────────


def cat_audit(rng: random.Random, idx: int) -> Built:
    regions = ["서울", "부산", "대구", "광주"]
    products = ["노트북", "모니터", "키보드", "마우스", "도킹스테이션"]
    price = {"노트북": 1450000, "모니터": 320000, "키보드": 89000, "마우스": 39000, "도킹스테이션": 210000}
    rows = []
    for i in range(140 + idx * 10):
        rows.append([f"S-{3000 + i}", rng.choice(regions), rng.choice(products), rng.randint(1, 6),
                     rng.choice(["완료"] * 6 + ["취소"])])
    done = [r for r in rows if r[4] == "완료"]
    truth: Dict[str, Any] = {}
    for reg in regions:
        sub = [r for r in done if r[1] == reg]
        truth[f"regions.{reg}.orders"] = len(sub)
        truth[f"regions.{reg}.revenue"] = sum(price[r[2]] * r[3] for r in sub)
    qty: Dict[str, int] = {}
    for r in done:
        qty[r[2]] = qty.get(r[2], 0) + r[3]
    for prod in products:
        truth[f"products.{prod}.qty"] = qty.get(prod, 0)
    truth["top_product_by_qty"] = sorted(qty.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    truth["cancelled"] = sum(1 for r in rows if r[4] == "취소")
    truth["total_revenue"] = sum(truth[f"regions.{reg}.revenue"] for reg in regions)
    fields = sorted(truth)
    wrong = sorted(rng.sample(fields, 8))
    draft: Dict[str, Any] = {}
    for f in fields:
        v = truth[f]
        if f in wrong:
            if isinstance(v, int):
                v = v + rng.choice([-1, 1]) * (rng.randint(1, 3) if v < 100 else rng.randint(1, 9) * 1000)
            else:
                v = rng.choice([p for p in products if p != v])
        draft[f] = v
    nested: Dict[str, Any] = {"regions": {}, "products": {}}
    for f, v in draft.items():
        if f.startswith(("regions.", "products.")):
            group, name, key = f.split(".")
            nested[group].setdefault(name, {})[key] = v
        else:
            nested[f] = v
    rules = (
        "# 보고서 계산 규칙\n\n"
        "- 집계는 status 가 '완료' 인 행만. revenue = Σ qty × 단가(아래 표). orders = 행 수.\n"
        "- cancelled = status 가 '취소' 인 행 수. total_revenue = 지역 revenue 합.\n"
        "- products.<제품>.qty = 완료 행의 그 제품 qty 합. top_product_by_qty = 그 합이 가장 큰 제품(같으면 이름 오름차순).\n\n"
        "| 제품 | 단가 |\n|---|---|\n" + "\n".join(f"| {p} | {v:,} |" for p, v in price.items()) + "\n"
    )
    out = {"corrections": [{"field": f, "correct": truth[f]} for f in wrong]}

    def oracle(ws: str) -> str:
        _write(ws, "corrections.json", json.dumps(out, ensure_ascii=False))
        return "corrections.json 저장"

    draft_text = json.dumps(nested, ensure_ascii=False, indent=2)
    checks: List[Dict[str, Any]] = [
        {"kind": "file_exists", "path": "corrections.json", "required": True},
        {"kind": "file_json", "path": "corrections.json", "keys": ["corrections"], "format": True, "name": "shape"},
        {"kind": "file_json", "path": "corrections.json", "lengths": {"corrections": len(wrong)}, "name": "only-wrong-fields"},
    ] + [{"kind": "file_json", "path": "corrections.json",
          "path_equals": {f"corrections.{i}.field": f, f"corrections.{i}.correct": truth[f]}, "name": f"fix-{f}"}
         for i, f in enumerate(wrong)] + [
        {"kind": "file_unchanged", "path": "report_draft.json", "sha256": _sha(draft_text), "name": "draft-preserved"},
    ]
    return Built(oracle=oracle, spec=TaskSpec(
        id=f"audit-{idx}",
        prompt=("동료가 sales.csv 로 만든 report_draft.json 을 RULES.md 대로 검산하세요. 틀린 항목만 골라 corrections.json 으로 "
                '저장합니다. 형식: {"corrections": [{"field": "regions.서울.revenue" 같은 점 경로, "correct": 맞는 값}, ...]} — '
                "field 오름차순(문자열 비교), 맞는 항목은 넣지 마세요. report_draft.json 은 고치지 마세요."),
        files={"sales.csv": _csv([["sale_id", "region", "product", "qty", "status"]] + rows), "report_draft.json": draft_text,
               "RULES.md": rules},
        checks=tuple(checks), max_iterations=MAX_ITERATIONS, tags=("audit",),
    ))


CATEGORIES = [cat_minutes, cat_ledger, cat_contacts, cat_config, cat_policy, cat_schedule, cat_search, cat_audit]


def build_with_oracles(seed: int = 20261003, per_category: int = 4) -> Tuple[List[Built], Dict[str, List[str]]]:
    rng = random.Random(seed)
    built: List[Built] = []
    evolve, heldout = [], []
    for cat in CATEGORIES:
        group = [cat(rng, i) for i in range(per_category)]
        built += group
        evolve += [b.spec.id for b in group[:-1]]
        heldout.append(group[-1].spec.id)
    smoke = [built[0].spec.id, built[per_category].spec.id]
    return built, {"evolve": evolve, "heldout": heldout, "smoke": smoke}


def build(seed: int = 20261003, per_category: int = 4) -> Tuple[List[TaskSpec], Dict[str, List[str]]]:
    built, splits = build_with_oracles(seed, per_category)
    return [b.spec for b in built], splits


def main(argv: List[str]) -> None:
    out = argv[1] if len(argv) > 1 else "suites/xgen-pro"
    tasks, splits = build()
    write_suite(SUITE_NAME, tasks, splits, out)
    print(json.dumps({"out": out, "tasks": len(tasks), "splits": {k: len(v) for k, v in splits.items()}}, ensure_ascii=False))


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv)
