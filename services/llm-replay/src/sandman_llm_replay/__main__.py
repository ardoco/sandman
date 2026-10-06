import os

import uvicorn

from sandman_llm_replay.app import Settings, create_app

try:
    app = create_app(Settings.from_env())
except ValueError as e:
    raise SystemExit(f"llm-replay: {e}") from e

uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
