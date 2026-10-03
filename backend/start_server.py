import os

# Set env var before importing
os.environ["ENABLE_INLINE_SCHEDULER"] = "false"

from app.core.config import Settings
from app.main import create_app

settings = Settings()
app = create_app(settings)

# `noqa: E402`--los tres imports de arriba tienen que ir despues del `os.environ`.
import uvicorn  # noqa: E402

uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")
