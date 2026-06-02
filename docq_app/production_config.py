from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


@dataclass(frozen=True)
class RuntimeEnvironment:
    name: str
    debug: bool
    database_url: str
    redis_url: str
    nats_url: str
    event_bus_backend: str
    nats_stream_prefix: str
    node_id: str
    default_tenant_key: str
    pii_encryption_key: str
    secret_key: str
    jwt_secret: str
    session_ttl_minutes: int
    max_request_bytes: int
    enable_rate_limits: bool
    enable_worker_runtime: bool
    enable_metrics: bool


def load_runtime_environment(base_dir: Path) -> RuntimeEnvironment:
    load_dotenv(base_dir / ".env")
    env_name = os.getenv("DOCQ_ENV", "development").strip().lower()
    debug = os.getenv("DOCQ_DEBUG", "").lower() in {"1", "true", "yes", "on"}
    default_db_path = os.getenv("DOCQ_DB_PATH", "/tmp/docq.db" if os.getenv("VERCEL") else str(base_dir / "docq.db"))
    database_url = os.getenv("DOCQ_DATABASE_URL", f"sqlite:///{default_db_path}")
    redis_url = os.getenv("DOCQ_REDIS_URL", "redis://redis:6379/0")
    nats_url = os.getenv("DOCQ_NATS_URL", "nats://localhost:4222")
    event_bus_backend = os.getenv("DOCQ_EVENT_BUS_BACKEND", "inprocess").strip().lower()
    nats_stream_prefix = os.getenv("DOCQ_NATS_STREAM_PREFIX", "docq")
    node_id = os.getenv("DOCQ_NODE_ID", f"docq-{env_name}-node")
    default_tenant_key = os.getenv("DOCQ_DEFAULT_TENANT_KEY", "default-clinic")
    pii_encryption_key = os.getenv("DOCQ_PII_ENCRYPTION_KEY", "docq-dev-pii-key")
    secret_key = os.getenv("DOCQ_SECRET_KEY", "change-me-in-env")
    jwt_secret = os.getenv("DOCQ_JWT_SECRET", secret_key)
    session_ttl_minutes = int(os.getenv("DOCQ_SESSION_TTL_MINUTES", "120"))
    max_request_bytes = int(os.getenv("DOCQ_MAX_REQUEST_BYTES", str(1024 * 1024)))
    enable_rate_limits = os.getenv("DOCQ_ENABLE_RATE_LIMITS", "true").lower() in {"1", "true", "yes", "on"}
    enable_worker_runtime = os.getenv("DOCQ_ENABLE_WORKERS", "true").lower() in {"1", "true", "yes", "on"}
    enable_metrics = os.getenv("DOCQ_ENABLE_METRICS", "true").lower() in {"1", "true", "yes", "on"}
    return RuntimeEnvironment(
        name=env_name,
        debug=debug,
        database_url=database_url,
        redis_url=redis_url,
        nats_url=nats_url,
        event_bus_backend=event_bus_backend,
        nats_stream_prefix=nats_stream_prefix,
        node_id=node_id,
        default_tenant_key=default_tenant_key,
        pii_encryption_key=pii_encryption_key,
        secret_key=secret_key,
        jwt_secret=jwt_secret,
        session_ttl_minutes=session_ttl_minutes,
        max_request_bytes=max_request_bytes,
        enable_rate_limits=enable_rate_limits,
        enable_worker_runtime=enable_worker_runtime,
        enable_metrics=enable_metrics,
    )
