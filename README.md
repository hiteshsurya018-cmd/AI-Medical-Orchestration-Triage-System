# DOCQ Pred

DOCQ is a Flask clinic-operations demo that combines intake triage, appointment routing, doctor inbox workflows, and notification automation.

## Improvements in this refactor

- Environment-based configuration for secrets, paths, delivery settings, and debug behavior
- Adaptive password hashing with `werkzeug.security`
- Atomic slot reservation inside a single SQLite transaction
- Reminder delivery moved to an explicit Flask CLI command: `flask --app app send-reminders`
- CSRF validation for POST requests plus safer session cookie defaults
- Safe post-login redirects
- Structured logging around SMTP and Twilio failures
- Basic pytest coverage for routing, appointment flow, reminders, and role protection
- Modular package split under `docq_app/`

## Local setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DOCQ_SECRET_KEY = "replace-this"
$env:DOCQ_N8N_CONFIRMATION_WEBHOOK = "https://active-pecan-unwrapped.ngrok-free.dev/webhook/docq-confirmation"
python app.py
```
