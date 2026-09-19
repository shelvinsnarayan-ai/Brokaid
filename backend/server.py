"""FastAPI entrypoint shim.

Kept at the backend root because supervisor invokes
`uvicorn server:app` from /app/backend (which is a symlink here).
The real application lives in `app.main`.
"""
from app.main import app  # noqa: F401
