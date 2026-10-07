import os

import uvicorn
from sandman_agent import ConfigError, telemetry

from sandman_query.app import build

telemetry.setup_logging("query-service", None)
try:
    app = build()
except ConfigError as e:
    raise SystemExit(f"query-service: {e}") from e

uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
