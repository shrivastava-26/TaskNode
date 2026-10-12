import json
import os
import re
import httpx

async def generate(system, message):
    provider = os.getenv("LLM_PROVIDER", "groq")
    if provider == "mock":
        if os.getenv("AI_ALLOW_MOCK") != "true":
            raise ValueError("Mock is restricted to explicit test mode")
        return mock(system, message)
    key = os.getenv("LLM_API_KEY")
    if not key:
        raise ValueError("LLM key missing")
    base = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    model = os.getenv("LLM_MODEL", "openai/gpt-oss-20b")
    tuning = {"reasoning_effort": "low"} if model.startswith("openai/gpt-oss") else {}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(base.rstrip("/")+"/chat/completions", headers={"Authorization": "Bearer "+key}, json={
            "model": model, "temperature": 0, "max_completion_tokens": 1200, **tuning,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": message}]})
        response.raise_for_status()
        return json.loads(response.json()["choices"][0]["message"]["content"])

def mock(system, message):
    """Deterministic test double, never presented as a real model evaluation."""
    question = json.loads(message).get("question", "").lower()
    if system.startswith("PLAN"):
        if re.search(r"\b(add|create|update|delete)\b", question):
            action = "delete" if "delete" in question else "update" if "update" in question else "create"
            ids = re.search(r"expense\s+(\d+)", question)
            amount = re.search(r"(?:usd|inr|eur)\s*(\d+(?:\.\d+)?)", question)
            return {"route": "crud", "draft": {"action": action, "expense_id": int(ids[1]) if ids else None,
                    "amount": amount[1] if amount else "10", "category": "Food", "currency": "INR" if "inr" in question else "USD"}}
        if any(w in question for w in ("spend", "spent", "total", "transactions")):
            return {"route": "query", "sql": "SELECT SUM(amount_usd) AS total_amount FROM ai_expenses WHERE expense_date >= CURRENT_DATE - INTERVAL '7 days' AND expense_date < CURRENT_DATE"}
        return {"route": "rag" if any(w in question for w in ("etl", "currency", "freshness", "schema", "deleted")) else "chat"}
    context = json.loads(message)
    if context.get("tool"):
        return {"answer": "Warehouse result: "+json.dumps(context["tool"]["rows"]), "citations": context["ids"]}
    if context["ids"]:
        return {"answer": "Spark updates the independent warehouse snapshot; dashboard freshness comes from MAX(loaded_at).", "citations": context["ids"]}
    return {"answer": "I can explain this expense platform, query analytics, or preview expense changes.", "citations": []}
