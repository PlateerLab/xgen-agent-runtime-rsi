"""외부 usage 의 auxiliary: 메인 루프 밖 호출(압축 등)도 호스트가 토큰 사용량에 더할 수 있게 따로 싣는다."""

from __future__ import annotations

from types import SimpleNamespace

from xgen_rsi.base.core.state import TokenUsage
from xgen_rsi.kernel.ledger import UsageLedger


def _resp(i: int, o: int, cache_read: int = 0) -> SimpleNamespace:
    return SimpleNamespace(usage=TokenUsage(input_tokens=i, output_tokens=o, cache_read_input_tokens=cache_read),
                           model="m", raw=None, stop_reason="end_turn")


def test_auxiliary_calls_are_reported_beside_the_main_loop():
    ledger = UsageLedger(provider="anthropic")
    ledger.record(purpose_name="main", response=_resp(100, 10), provider="anthropic", duration_ms=1)
    ledger.record(purpose_name="compact", response=_resp(900, 80, cache_read=50), provider="anthropic", duration_ms=1)
    ledger.record_failure(purpose_name="compact", provider="anthropic", model="m", duration_ms=1, error="boom")
    usage = ledger.external_usage_payload(model="m", provider="anthropic", turn_cost_usd=0.0, harness=None)
    assert (usage["input_tokens"], usage["output_tokens"]) == (100, 10), "최상위는 메인 루프만(예전 의미 그대로)"
    aux = usage["auxiliary"]
    assert (aux["input_tokens"], aux["output_tokens"], aux["cache_read_tokens"], aux["calls"]) == (900, 80, 50, 1)
    assert aux["by_purpose"]["compact"]["calls"] == 1, "실패한 호출은 세지 않는다"


def test_auxiliary_only_turn_still_reports():
    ledger = UsageLedger(provider="openai")
    ledger.record(purpose_name="compact", response=_resp(7, 3), provider="openai", duration_ms=1)
    usage = ledger.external_usage_payload(model="m", provider="openai", turn_cost_usd=0.0, harness=None)
    assert usage["input_tokens"] == 0 and usage["auxiliary"]["input_tokens"] == 7
