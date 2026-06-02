from __future__ import annotations

from ..notification_adapters import post_slack_webhook, post_webhook_notification, send_email_notification
from . import record_integration_health


def _status_payload(*, provider: str, tenant_key: str, configured: bool, activated: bool, detail: str, metadata: dict[str, object] | None = None) -> dict[str, object]:
    status = "active" if activated else "ready" if configured else "config-missing"
    record_integration_health(provider_key=provider, tenant_key=tenant_key, status=status, detail=detail, metadata=metadata or {})
    return {"provider": provider, "tenant_key": tenant_key, "status": status, "detail": detail, "configured": configured, "activated": activated, "metadata": metadata or {}}


def twilio_sms_adapter(*, tenant_key: str, target: str, message: str, account_sid: str | None = None, from_number: str | None = None) -> dict[str, object]:
    configured = bool(account_sid and from_number and target)
    return _status_payload(
        provider="twilio_sms",
        tenant_key=tenant_key,
        configured=configured,
        activated=False,
        detail="sms adapter validation complete" if configured else "missing Twilio account SID or from number",
        metadata={"target_preview": target[-4:] if target else "", "message_length": len(message)},
    )


def twilio_whatsapp_adapter(*, tenant_key: str, target: str, message: str, account_sid: str | None = None, from_number: str | None = None) -> dict[str, object]:
    configured = bool(account_sid and from_number and target)
    return _status_payload(
        provider="twilio_whatsapp",
        tenant_key=tenant_key,
        configured=configured,
        activated=False,
        detail="whatsapp adapter validation complete" if configured else "missing Twilio account SID or WhatsApp sender",
        metadata={"target_preview": target[-4:] if target else "", "message_length": len(message)},
    )


def sendgrid_email_adapter(
    *,
    tenant_key: str,
    target: str,
    subject: str,
    body: str,
    smtp_host: str | None = None,
    smtp_port: int = 465,
    smtp_username: str | None = None,
    smtp_password: str | None = None,
    smtp_from: str | None = None,
    activate: bool = False,
) -> dict[str, object]:
    configured = bool(smtp_host and smtp_from and target)
    activated = False
    detail = "email adapter validation complete" if configured else "missing SMTP host or sender"
    if activate and configured:
        delivered, error = send_email_notification(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_username=smtp_username,
            smtp_password=smtp_password,
            smtp_from=smtp_from,
            recipient=target,
            subject=subject,
            body=body,
        )
        activated = delivered
        detail = "email delivery acknowledged" if delivered else str(error or "email delivery failed")
    return _status_payload(
        provider="sendgrid_email",
        tenant_key=tenant_key,
        configured=configured,
        activated=activated,
        detail=detail,
        metadata={"target": target, "subject": subject},
    )


def google_calendar_adapter(*, tenant_key: str, appointment_ref: str, client_id: str | None = None, refresh_token: str | None = None) -> dict[str, object]:
    configured = bool(client_id and refresh_token)
    return _status_payload(
        provider="google_calendar",
        tenant_key=tenant_key,
        configured=configured,
        activated=False,
        detail="calendar OAuth contract present" if configured else "missing Google OAuth credentials",
        metadata={"appointment_ref": appointment_ref},
    )


def outlook_calendar_adapter(*, tenant_key: str, appointment_ref: str, client_id: str | None = None, tenant_id: str | None = None, refresh_token: str | None = None) -> dict[str, object]:
    configured = bool(client_id and tenant_id and refresh_token)
    return _status_payload(
        provider="outlook_calendar",
        tenant_key=tenant_key,
        configured=configured,
        activated=False,
        detail="calendar OAuth contract present" if configured else "missing Outlook OAuth credentials",
        metadata={"appointment_ref": appointment_ref},
    )


def slack_webhook_adapter(*, tenant_key: str, message: str, webhook_url: str | None = None, activate: bool = False) -> dict[str, object]:
    configured = bool(webhook_url)
    activated = False
    detail = "slack webhook configured" if configured else "missing Slack webhook URL"
    if activate and configured:
        delivered, error = post_slack_webhook(webhook_url=webhook_url, text=message)
        activated = delivered
        detail = "slack alert acknowledged" if delivered else str(error or "slack delivery failed")
    return _status_payload(
        provider="slack_webhook",
        tenant_key=tenant_key,
        configured=configured,
        activated=activated,
        detail=detail,
        metadata={"message_length": len(message)},
    )


def webhook_delivery_adapter(*, tenant_key: str, webhook_url: str | None, payload: dict[str, object], activate: bool = False) -> dict[str, object]:
    configured = bool(webhook_url)
    activated = False
    detail = "generic webhook configured" if configured else "missing webhook URL"
    if activate and configured:
        delivered, error = post_webhook_notification(webhook_url=webhook_url, payload=payload)
        activated = delivered
        detail = "webhook delivery acknowledged" if delivered else str(error or "webhook delivery failed")
    return _status_payload(
        provider="generic_webhook",
        tenant_key=tenant_key,
        configured=configured,
        activated=activated,
        detail=detail,
        metadata={"payload_keys": sorted(payload.keys())},
    )
