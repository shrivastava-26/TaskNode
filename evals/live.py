"""Optional real-provider checks. Preview-only CRUD; requires a running AI app."""
import argparse
import os
from pathlib import Path
from uuid import uuid4
import httpx
import yaml
from dotenv import load_dotenv

load_dotenv()
parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://localhost:8000")
args = parser.parse_args()
cases = yaml.safe_load(Path(__file__).with_name("eval_cases.yaml").read_text())
passed = 0
for case in cases:
    response = httpx.post(args.url+"/chat", headers={"X-Chat-Token": os.environ["CHAT_ACCESS_TOKEN"]},
        json={"message": case["message"], "session_id": str(uuid4())}, timeout=70)
    result = response.json()
    ok = response.status_code == 200 and not result.get("test_mode") and result.get("route") == case["route"]
    if case["route"] in {"rag", "query"}:
        ok = ok and bool(result.get("citations"))
    if case["route"] == "query":
        ok = ok and bool(result.get("sql"))
    ok = ok and "person@example.com" not in str(result)
    passed += bool(ok)
    print(f"{case['name']}: {'PASS' if ok else 'FAIL'}")
    if result.get("trace_id") and os.getenv("LANGFUSE_SECRET_KEY"):
        from langfuse import get_client
        get_client().create_score(trace_id=result["trace_id"], name="live:"+case["name"], value=int(ok))
print(f"Live provider cases: {passed}/{len(cases)}. These checks do not measure full answer quality.")
raise SystemExit(0 if passed >= 11 else 1)
