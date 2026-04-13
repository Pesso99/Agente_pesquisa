"""Diagnostic: show dead letters for a job."""
import json
import sqlite3
import sys

job_id = sys.argv[1] if len(sys.argv) > 1 else "test_llm_002"
conn = sqlite3.connect("data/state/runtime.db")
conn.row_factory = sqlite3.Row

rows = conn.execute(
    "SELECT stage, record_id, error_message, payload_json FROM dead_letters WHERE job_id=?",
    (job_id,),
).fetchall()

for r in rows:
    payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
    print(f"[{r['stage']}] {r['record_id']}")
    print(f"  error: {r['error_message'][:200]}")
    for key in ("llm_reasoning", "source_quality_label", "source_url", "blocking_reasons"):
        if key in payload:
            print(f"  {key}: {payload[key]}")
    print()

# Agent messages
rows2 = conn.execute(
    "SELECT source_agent, target_agent, message_type, body_json FROM agent_messages WHERE job_id=?",
    (job_id,),
).fetchall()
print("--- Agent Messages ---")
for r in rows2:
    body = json.loads(r["body_json"])
    print(f"  {r['source_agent']} -> {r['target_agent']} [{r['message_type']}]")
    if "quality_label" in body:
        print(f"    quality_label={body['quality_label']}, blocked={body.get('blocked')}")
    print()

conn.close()
