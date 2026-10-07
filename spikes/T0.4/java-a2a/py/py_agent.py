"""Python test agent: tlr-agent card + dummy recover-trace-links skill. uv run python spikes/T0.4/java-a2a/py/py_agent.py PORT"""

import asyncio
import hashlib
import os
import sys
from pathlib import Path
from typing import Any

import uvicorn

from sandman_agent import Settings, new_run_id
from sandman_agent.a2a import SkillContext, SkillResult, serve

ROOT = Path(__file__).resolve().parents[4]
TOKEN = os.environ.get("SPIKE_TOKEN", "spike-token")


class Dummy:
    id = "recover-trace-links"

    async def run(self, ctx: SkillContext, input: dict[str, Any]) -> SkillResult:
        print("INPUT", input, flush=True)
        for _ in range(int(input.get("params", {}).get("sleepMs", 0)) // 50):
            if ctx.cancelled.is_set():
                break
            await asyncio.sleep(0.05)
        n = len(input["sourceVersionIds"]) * len(input["targetVersionIds"])
        run = {"approach": "stub-echo", "approachVersion": "0", "runId": new_run_id(), "status": "completed", "linkCount": n}
        return SkillResult({"runs": [run]})  # plugin-like: no completion event (no NATS in this spike)


settings = Settings(
    service_name="tlr-agent",
    contracts_dir=ROOT / "contracts",
    service_verify={"orchestrator": hashlib.sha256(TOKEN.encode()).hexdigest()},
)
app = serve(ROOT / "contracts/agent-cards/tlr-agent.json", [Dummy()], settings)
uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
