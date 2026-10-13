"""Offline regression tests use an explicit fake model and fake dependencies."""
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import pytest
import yaml
from fastapi.testclient import TestClient

os.environ.update(AI_ENABLED="true", LLM_PROVIDER="mock", AI_ALLOW_MOCK="true",
                  CHAT_ACCESS_TOKEN="test-chat-access-token-with-32-characters", WRITE_APPROVAL_KEY="test-write-approval-key-with-32-characters")
from app.main import app
from ai.security import safe_sql, redact, sign, verify, grounded

CASES = yaml.safe_load(Path(__file__).with_name("eval_cases.yaml").read_text())
DOCS = [{"id": "metric:overview", "content": "Spark updates the independent warehouse. Data freshness is MAX(loaded_at)."}]

@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_case(case):
    with patch("agent.api.log") as audit, patch("agent.graph.retrieve", return_value=DOCS), patch("agent.graph.call_tool", new_callable=AsyncMock) as tool:
        tool.return_value = {"sql": "SELECT SUM(amount_usd) FROM ai_expenses LIMIT 100", "rows": [{"total_amount": "25.00"}], "latest_load": "2026-10-12"}
        result = TestClient(app).post("/chat", headers={"X-Chat-Token": os.environ["CHAT_ACCESS_TOKEN"]},
            json={"session_id": str(uuid4()), "message": case["message"]}).json()
        assert result["route"] == case["route"]
        assert case["keyword"].lower() in result["response"].lower()
        assert "person@example.com" not in str(result)
        assert tool.await_count == (1 if case["route"] == "query" else 0)
        if case["route"] in {"rag", "query"}:
            assert result["citations"] == ["metric:overview"]
        assert audit.called
        if result.get("trace_id"):
            from langfuse import get_client
            get_client().create_score(trace_id=result["trace_id"], name="eval:"+case["name"], value=1)
        print(case["name"]+": PASS")

@pytest.mark.parametrize("query", ["DELETE FROM ai_expenses", "SELECT * FROM fact_expenses", "SELECT * FROM information_schema.tables", "SELECT * FROM ai_expenses; DROP TABLE x", "SELECT pg_sleep(5) FROM ai_expenses", "SELECT * INTO x FROM ai_expenses", "SELECT * FROM ai_expenses FOR UPDATE", "WITH x AS(SELECT * FROM ai_expenses) SELECT * FROM x", "SELECT * FROM ai_expenses JOIN ai_daily ON true", "SELECT 'person@example.com' FROM ai_expenses"])
def test_sql_rejected(query):
    with pytest.raises(ValueError):
        safe_sql(query)

def test_policy_details():
    assert safe_sql("SELECT * FROM ai_expenses LIMIT 1000").endswith("LIMIT 100")
    assert redact("test@example.com 999-12-3456 9876543210")[1] == 3
    assert redact("2026-10-12")[1] == 0
    assert not grounded("Spend is 999", '{"total": "25"}')
    token = sign({"expires": 0, "draft": {"action": "create"}})
    with pytest.raises(ValueError):
        verify(token)
