from __future__ import annotations

import os
from pathlib import Path

from .production_config import load_runtime_environment


class Config:
    def __init__(self) -> None:
        base_dir = Path(__file__).resolve().parent.parent
        runtime = load_runtime_environment(base_dir)
        self.BASE_DIR = Path(os.getenv("DOCQ_BASE_DIR", str(base_dir)))
        self.DATASET_PATH = Path(os.getenv("DOCQ_DATASET_PATH", str(self.BASE_DIR / "final.csv")))
        default_db_path = "/tmp/docq.db" if os.getenv("VERCEL") else str(self.BASE_DIR / "docq.db")
        self.DB_PATH = Path(os.getenv("DOCQ_DB_PATH", default_db_path))
        self.MODEL_DIR = Path(os.getenv("DOCQ_MODEL_DIR", str(self.BASE_DIR / "models")))
        self.SECRET_KEY = runtime.secret_key
        self.JWT_SECRET = runtime.jwt_secret
        self.DEBUG = runtime.debug
        self.ENV_NAME = runtime.name
        self.DATABASE_URL = runtime.database_url
        self.SQLALCHEMY_DATABASE_URI = runtime.database_url
        self.REDIS_URL = runtime.redis_url
        self.NATS_URL = runtime.nats_url
        self.EVENT_BUS_BACKEND = runtime.event_bus_backend
        self.NATS_STREAM_PREFIX = runtime.nats_stream_prefix
        self.NODE_ID = runtime.node_id
        self.DEFAULT_TENANT_KEY = runtime.default_tenant_key
        self.PII_ENCRYPTION_KEY = runtime.pii_encryption_key
        self.ENABLE_RATE_LIMITS = runtime.enable_rate_limits
        self.ENABLE_WORKERS = runtime.enable_worker_runtime
        self.ENABLE_METRICS = runtime.enable_metrics
        self.SESSION_TTL_MINUTES = runtime.session_ttl_minutes
        self.MAX_CONTENT_LENGTH = runtime.max_request_bytes
        self.SEED_DEMO_USERS = os.getenv("DOCQ_SEED_DEMO_USERS", "true").lower() in {"1", "true", "yes", "on"}
        self.SEED_SLOTS = os.getenv("DOCQ_SEED_SLOTS", "true").lower() in {"1", "true", "yes", "on"}
        self.LOAD_MODELS_ON_STARTUP = os.getenv("DOCQ_LOAD_MODELS_ON_STARTUP", "true").lower() in {"1", "true", "yes", "on"}
        self.SESSION_COOKIE_HTTPONLY = True
        self.SESSION_COOKIE_SAMESITE = "Lax"
        self.SESSION_COOKIE_SECURE = os.getenv("DOCQ_SESSION_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"} or runtime.name == "production"
        self.SMTP_HOST = os.getenv("SMTP_HOST")
        self.SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
        self.SMTP_USERNAME = os.getenv("SMTP_USERNAME")
        self.SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
        self.SMTP_FROM = os.getenv("SMTP_FROM")
        self.TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
        self.TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
        self.TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")
        self.TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM")
        self.TWILIO_WHATSAPP_SANDBOX_JOIN_CODE = os.getenv("TWILIO_WHATSAPP_SANDBOX_JOIN_CODE")
        self.CRON_SECRET = os.getenv("CRON_SECRET", os.getenv("DOCQ_CRON_SECRET", ""))
        self.DOCQ_N8N_CONFIRMATION_WEBHOOK = os.getenv(
            "DOCQ_N8N_CONFIRMATION_WEBHOOK",
            "https://active-pecan-unwrapped.ngrok-free.dev/webhook/docq-confirmation",
        )
        self.SLACK_WEBHOOK_URL = os.getenv("DOCQ_SLACK_WEBHOOK_URL")
        self.GENERIC_WEBHOOK_URL = os.getenv("DOCQ_GENERIC_WEBHOOK_URL")
        self.GOOGLE_CALENDAR_CLIENT_ID = os.getenv("DOCQ_GOOGLE_CALENDAR_CLIENT_ID")
        self.GOOGLE_CALENDAR_REFRESH_TOKEN = os.getenv("DOCQ_GOOGLE_CALENDAR_REFRESH_TOKEN")
        self.OUTLOOK_CALENDAR_CLIENT_ID = os.getenv("DOCQ_OUTLOOK_CALENDAR_CLIENT_ID")
        self.OUTLOOK_CALENDAR_TENANT_ID = os.getenv("DOCQ_OUTLOOK_CALENDAR_TENANT_ID")
        self.OUTLOOK_CALENDAR_REFRESH_TOKEN = os.getenv("DOCQ_OUTLOOK_CALENDAR_REFRESH_TOKEN")
        self.ENABLE_EXTERNAL_INTEGRATIONS = os.getenv("DOCQ_ENABLE_EXTERNAL_INTEGRATIONS", "false").lower() in {"1", "true", "yes", "on"}
        self.OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("DOCQ_OTEL_EXPORTER_OTLP_ENDPOINT")
