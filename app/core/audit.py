import json
from datetime import datetime, timezone
from pathlib import Path

LOG_FILE = Path("attack_log.jsonl")


def record_interaction(user, module_id: str, prompt: str, response: str) -> None:
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "module": module_id,
        "user": user.email,
        "role": user.role,
        "prompt": prompt,
        "response": response,
    }
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")
