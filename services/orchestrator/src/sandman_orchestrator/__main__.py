import os

import uvicorn
from sandman_agent import ConfigError, telemetry

from sandman_orchestrator.app import build

telemetry.setup_logging("orchestrator", None)
try:
    app = build()
except ConfigError as e:
    raise SystemExit(f"orchestrator: {e}") from e

uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
