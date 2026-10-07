"""Python A2aClient against any SANDMAN agent. uv run python spikes/T0.4/java-a2a/py/py_client.py BASE_URL"""

import asyncio
import json
import os
import sys
from pathlib import Path

from sandman_agent.a2a import INVOCATION_EXTENSION, A2aClient, TaskNotFoundError

ROOT = Path(__file__).resolve().parents[4]
INPUT = {
    "projectId": "mediastore",
    "sourceVersionIds": ["ver_0123456789abcdef0123456789abcdef", "ver_1123456789abcdef0123456789abcdef"],
    "targetVersionIds": ["ver_fedcba9876543210fedcba9876543210"],
}


async def until_terminal(c: A2aClient, url: str, snap):
    for _ in range(300):
        if snap.terminal:
            return snap
        await asyncio.sleep(0.1)
        snap = await c.get(url, snap.task_id)
    return snap


async def main(url: str) -> None:
    c = A2aClient(os.environ.get("SPIKE_TOKEN", "spike-token"))
    if os.environ.get("SPIKE_EXT_HEADER"):  # a2a-java rejects calls that do not request the card's required extension
        c._http.headers["A2A-Extensions"] = INVOCATION_EXTENSION
    try:
        cases = json.loads((ROOT / "contracts/conformance/vectors.json").read_text())["skillInvocation"]["cases"]
        for case in cases:
            try:
                s = await until_terminal(c, url, await c.send(url, case["message"]))
                print(f"CASE {case['name']:<40} expected={case['expectedState']}/{case.get('expectedErrorCode')} got={s}")
            except Exception as e:  # noqa: BLE001
                print(f"CASE {case['name']:<40} ERROR {type(e).__name__}: {e}")
        s = await until_terminal(c, url, await c.submit(url, "recover-trace-links", INPUT, workflow_id="wf_0123456789abcdef0123456789abcdef"))
        print("VALID", s, "linkCount type:", type(s.result["runs"][0]["linkCount"]).__name__ if s.result else "-")
        try:
            await c.get(url, "does-not-exist")
        except TaskNotFoundError as e:
            print("GETTASK unknown -> TaskNotFoundError", e)
        except Exception as e:  # noqa: BLE001
            print("GETTASK unknown ->", type(e).__name__, e)
        s = await c.submit(url, "recover-trace-links", {**INPUT, "params": {"sleepMs": 5000}})
        print("CANCEL submitted", s)
        print("CANCEL response", await c.cancel(url, s.task_id))
        await asyncio.sleep(0.3)
        print("CANCEL get", await c.get(url, s.task_id))
    finally:
        await c.close()


asyncio.run(main(sys.argv[1]))
