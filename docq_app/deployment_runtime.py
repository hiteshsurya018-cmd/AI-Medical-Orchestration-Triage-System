from __future__ import annotations

from pathlib import Path

from .production_db import check_database_readiness


def build_startup_dependency_report(config: dict[str, object]) -> dict[str, object]:
    checks = {
        "database": check_database_readiness(str(config.get("DATABASE_URL", ""))),
        "event_bus": bool(config.get("NATS_URL")),
        "redis": bool(config.get("REDIS_URL")),
        "tenant": bool(config.get("DEFAULT_TENANT_KEY")),
        "secret_key": bool(config.get("SECRET_KEY") and config.get("SECRET_KEY") != "change-me-in-env"),
        "jwt_secret": bool(config.get("JWT_SECRET")),
        "pii_encryption": bool(config.get("PII_ENCRYPTION_KEY")),
    }
    critical_failures = [key for key, value in checks.items() if not value and key in {"database", "secret_key", "jwt_secret", "pii_encryption"}]
    return {
        "checks": checks,
        "critical_failures": critical_failures,
        "ready": not critical_failures,
    }


def validate_environment_contract(config: dict[str, object]) -> dict[str, object]:
    required_keys = [
        "DATABASE_URL",
        "REDIS_URL",
        "NATS_URL",
        "SECRET_KEY",
        "JWT_SECRET",
        "DEFAULT_TENANT_KEY",
        "PII_ENCRYPTION_KEY",
    ]
    missing = [key for key in required_keys if not config.get(key)]
    weak_defaults = [key for key in ("SECRET_KEY", "JWT_SECRET", "PII_ENCRYPTION_KEY") if str(config.get(key, "")).startswith("replace-") or str(config.get(key, "")).startswith("change-")]
    return {
        "missing": missing,
        "weak_defaults": weak_defaults,
        "runtime_env": str(config.get("ENV_NAME", "development")),
        "event_bus_backend": str(config.get("EVENT_BUS_BACKEND", "inprocess")),
    }


def build_deployment_manifest_summary(base_dir: Path) -> dict[str, object]:
    helm_dir = base_dir / "helm" / "docq"
    k8s_dir = base_dir / "k8s"
    return {
        "helm_present": helm_dir.exists(),
        "helm_templates": sorted(str(path.relative_to(base_dir)) for path in helm_dir.rglob("*.yaml")),
        "k8s_present": k8s_dir.exists(),
        "k8s_manifests": sorted(str(path.relative_to(base_dir)) for path in k8s_dir.rglob("*.yaml")),
        "dockerfile_present": (base_dir / "Dockerfile").exists(),
        "compose_present": (base_dir / "docker-compose.yml").exists(),
    }


def build_deployment_health_panel(config: dict[str, object], base_dir: Path) -> dict[str, object]:
    dependency_report = build_startup_dependency_report(config)
    environment_report = validate_environment_contract(config)
    manifest_summary = build_deployment_manifest_summary(base_dir)
    return {
        "startup": dependency_report,
        "environment": environment_report,
        "manifests": manifest_summary,
        "rolling_deploy_ready": dependency_report["ready"] and not environment_report["missing"],
    }
