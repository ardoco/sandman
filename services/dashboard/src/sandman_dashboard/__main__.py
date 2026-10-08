import os

import uvicorn
from sandman_agent import ConfigError, telemetry

from sandman_dashboard.app import build

telemetry.setup_logging("dashboard", None)
try:
    app = build()
except (ConfigError, ValueError) as e:
    raise SystemExit(f"dashboard: {e}") from e

uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
