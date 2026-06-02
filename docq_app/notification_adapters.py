from __future__ import annotations

import json
import smtplib
from email.message import EmailMessage
from urllib import request as urlrequest


def send_email_notification(*, smtp_host: str | None, smtp_port: int, smtp_username: str | None, smtp_password: str | None, smtp_from: str | None, recipient: str, subject: str, body: str) -> tuple[bool, str | None]:
    if not smtp_host or not smtp_from:
        return False, "email transport not configured"
    message = EmailMessage()
    message["From"] = smtp_from
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10) as client:
            if smtp_username and smtp_password:
                client.login(smtp_username, smtp_password)
            client.send_message(message)
        return True, None
    except Exception as exc:  # pragma: no cover - integration path
        return False, str(exc)


def post_webhook_notification(*, webhook_url: str | None, payload: dict[str, object]) -> tuple[bool, str | None]:
    if not webhook_url:
        return False, "webhook transport not configured"
    body = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(webhook_url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=10) as response:  # pragma: no cover - integration path
            if 200 <= response.status < 300:
                return True, None
            return False, f"webhook returned {response.status}"
    except Exception as exc:  # pragma: no cover - integration path
        return False, str(exc)


def post_slack_webhook(*, webhook_url: str | None, text: str) -> tuple[bool, str | None]:
    return post_webhook_notification(webhook_url=webhook_url, payload={"text": text})
