"""Each unit test starts with a fresh process-level billing state."""
import pytest


@pytest.fixture(autouse=True)
def isolate_billing_halt(monkeypatch):
    from src import llm_client
    monkeypatch.setattr(llm_client,'BILLING_HALT_REASON',None)
