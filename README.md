# AI-Medical-Orchestration-Triage-System

DOCQ is a Flask-based AI medical orchestration and triage system. It is designed as a clinic operations platform, not a simple symptom chatbot: patients move through safe intake, emergency gating, department routing, doctor matching, appointment booking, doctor review, prescriptions, follow-up, and operational audit trails.

## Links

- Live demo: https://ai-medical-orchestration-triage-sys.vercel.app/
- Portfolio: https://portfolio-hitesh-surya.vercel.app/
- Repository: https://github.com/hiteshsurya018-cmd/AI-Medical-Orchestration-Triage-System
- Vercel project: https://vercel.com/hiteshsurya018-5475s-projects/ai-medical-orchestration-triage-system

## What This Project Does

- Guides patients through symptom intake, clinical questionnaire prompts, vitals capture, and profile-aware context.
- Detects emergency red flags before normal scheduling and bypasses appointment booking for critical cases.
- Routes non-emergency patients to clinically sensible departments such as General Medicine, Pediatrics, Orthopedics, Cardiology, Dermatology, Psychiatry, and OB-GYN.
- Recommends doctors and slots using department availability, continuity, workload, and schedule governance.
- Supports authenticated patient, doctor, admin, operations, audit, governance, and clinic staff workspaces.
- Lets doctors manage inboxes, SOAP-style notes, prescriptions, report reviews, care plans, follow-ups, and emergency cases.
- Records canonical workflow events for replay, drift review, model comparison, operational analytics, and audit export.
- Exposes production-oriented surfaces for metrics, readiness, deployment validation, integrations, chaos checks, and load benchmarks.

## Core User Journey

```text
Patient
Safe Triage
Correct Routing
Real Appointment
Doctor Consultation
Prescription
Follow-Up
```

The main product decision rule is that features should strengthen this care journey. Dashboards, governance, replay, and integrations support that flow instead of replacing it.

## Architecture

The app is a modular Flask application under `docq_app/`.

- `app.py` creates the Flask app through `docq_app.create_app()`.
- `docq_app/__init__.py` registers routes, guards, app lifecycle hooks, CLI jobs, and workspace views.
- `docq_app/workflow_engine.py` coordinates the case workflow through memory, intake, questionnaire, vitals, risk, policy, emergency escalation, scheduling, and communication agents.
- `docq_app/agents/` contains focused workflow agents for intake, vitals, risk, emergency escalation, scheduling, memory, questionnaire, and communication.
- `docq_app/appointments.py` owns appointment persistence, workflow event persistence, governance timelines, audit records, notifications, prescriptions, care plans, and scheduling state.
- `docq_app/ml.py`, `model_evaluation.py`, `ml_governance.py`, and `governance_runtime.py` handle routing models, evaluation, drift, rollout profiles, and governance recommendations.
- `docq_app/dashboard.py`, `analytics.py`, `observability.py`, and `runtime_topology.py` build operational dashboards, workflow metrics, topology views, and Prometheus-compatible metrics.
- `docq_app/integrations/` contains adapters for email, Twilio SMS/WhatsApp, Google Calendar, Outlook Calendar, Slack, and generic webhooks.
- `templates/` and `static/` provide the patient, doctor, admin, login, onboarding, observability, and scheduling UI.

## Workflow Orchestration

Each intake run creates a replayable workflow trail:

1. `memory-agent` hydrates patient context.
2. `intake-agent` extracts symptoms and missing information.
3. `questionnaire-agent` adds structured clinical follow-up questions when required.
4. `vitals-agent` evaluates submitted vitals.
5. `risk-agent` estimates urgency and routing risk.
6. `policy-engine` decides whether to escalate or schedule.
7. `emergency-escalation-agent` creates an emergency pathway for critical cases.
8. `scheduling-agent` builds doctor, date, and slot recommendations for non-emergency cases.
9. `communication-agent` prepares the patient response.

The system stores workflow IDs, trace IDs, causation links, branch IDs, confidence, reasons, payloads, and model lineage so decisions can be replayed and compared later.

## Main Capabilities

### Patient Experience

- Guest or authenticated intake
- Patient signup, login, email verification, and password reset flows
- Profile, medical history, age, phone, preferences, and communication settings
- Emergency assessment entry points
- Appointment booking with recommended specialty, doctor, dates, and slots
- Patient dashboard for appointments, assigned doctor, reports, prescriptions, timeline, and profile

### Clinical Routing and Scheduling

- Rule and ML-assisted department classification
- General Medicine fallback for uncertain symptoms
- Emergency bypass for red-flag symptoms
- Doctor recommendation and continuity-aware matching
- Slot seeding, reservation, rescheduling, reassignment, escalation, and cancellation
- Schedule governance snapshots for operations teams

### Doctor Workspace

- Doctor inbox and dashboard
- Pending, emergency, follow-up, monitoring, and report review queues
- Clinical diary and doctor notes
- Prescriptions and prescription-ready notifications
- Care plans and follow-up status
- Report upload and structured report analysis

### Admin and Operations

- Admin command center
- Doctor CRUD and availability management
- Appointment reschedule, reassign, and escalation APIs
- Workflow event feed, replay view, incident console, runtime queues, worker status, notifications, prescriptions, audit, continuity, and schedules
- Tenant-scoped analytics, compliance audit export, disaster recovery export, and provider coordination

### Governance, Replay, and ML Safety

- Canonical workflow event store
- Workflow replay and integrity APIs
- Workflow diff, model diff, drift, active workflow, summary, anomaly, intelligence, and metrics endpoints
- Offline model evaluations with promotion gates
- Continuous governance recommendations, timelines, drift triggers, and rollout simulations
- Append-only lineage and deterministic replay checks

### Integrations and Runtime

- SMTP and SendGrid-style email
- Twilio SMS and WhatsApp sandbox support
- Slack and generic webhooks
- Google Calendar and Outlook Calendar scaffolding
- In-process event bus by default, with NATS JetStream-oriented configuration
- Redis/RQ worker runtime configuration
- Prometheus metrics, topology, deployment validation, chaos experiments, and load benchmark hooks

## Tech Stack

- Python
- Flask
- SQLite for local/demo persistence
- SQLAlchemy and Alembic scaffolding
- Pandas and scikit-learn
- Pydantic
- Redis/RQ runtime configuration
- NATS client configuration
- Tailwind CDN templates and vanilla JavaScript UI
- Pytest
- Docker Compose, Kubernetes manifests, Helm chart, and Vercel serverless deployment config

## Key Routes

- `/` and `/intake` - patient intake workspace
- `/login`, `/patient-login`, `/doctor-login`, `/clinic-login` - role-aware login flows
- `/patient/dashboard` - patient care dashboard
- `/doctor/inbox` and `/doctor/dashboard` - doctor workspace
- `/dashboard` and `/admin` - operational dashboards
- `/api/intake` - orchestration intake API
- `/api/public-booking` - public appointment booking API
- `/api/reports/upload` - report upload and analysis
- `/api/workflows/<workflow_id>/events` - canonical workflow events
- `/api/workflows/<workflow_id>/replay` - replay-safe workflow reconstruction
- `/api/workflows/stream` - server-sent workflow console stream
- `/api/ml/governance/state` - governance runtime state
- `/api/analytics/operational` - tenant-scoped analytics
- `/api/integrations/health` - integration health summary
- `/health`, `/ready`, `/metrics` - runtime health and metrics

## Local Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

Open `http://localhost:5000`.

For a minimal setup without copying `.env.example`, set at least:

```powershell
$env:DOCQ_SECRET_KEY = "replace-this-with-a-long-random-secret"
$env:DOCQ_JWT_SECRET = "replace-this-with-a-second-long-random-secret"
$env:DOCQ_PII_ENCRYPTION_KEY = "replace-this-with-a-long-random-pii-key"
python app.py
```

## Demo Accounts

When `DOCQ_SEED_DEMO_USERS=true`, the app seeds demo users:

| Role | Email | Password |
| --- | --- | --- |
| Patient | `patient@docq.local` | `patient123` |
| Doctor | `cardio@docq.local` | `doctor123` |
| Admin | `admin@docq.local` | `admin123` |
| Receptionist | `desk@docq.local` | `desk123` |
| Governance analyst | `governance@docq.local` | `governance123` |
| Auditor | `auditor@docq.local` | `auditor123` |

These accounts are for local/demo use only.

## Useful Commands

```powershell
flask --app app send-reminders
flask --app app process-notifications
flask --app app seed-slots
flask --app app retrain-models
flask --app app escalate-stale-cases
flask --app app seed-demo
flask --app app run-benchmarks
```

## Testing

```powershell
pytest
```

The test suite covers routing, appointment creation, reminders, role protection, emergency escalation, report upload, workflow engine coordination, event persistence, replay APIs, model diff/drift, governance APIs, observability, admin operations, and append-only workflow protections.

## Deployment

### Vercel Demo

The repository includes `vercel.json` and `app.py` for Flask serverless deployment. On Vercel, SQLite uses `/tmp/docq.db`, which is suitable for demo/preview usage but not durable production storage.

Required production-like Vercel environment variables:

```text
DOCQ_SECRET_KEY=<long random secret>
DOCQ_JWT_SECRET=<second long random secret>
DOCQ_PII_ENCRYPTION_KEY=<long random pii key>
DOCQ_SEED_DEMO_USERS=true
DOCQ_SEED_SLOTS=true
DOCQ_LOAD_MODELS_ON_STARTUP=true
DOCQ_ENABLE_RATE_LIMITS=true
DOCQ_ENABLE_METRICS=true
DOCQ_ENABLE_EXTERNAL_INTEGRATIONS=false
CRON_SECRET=<random cron authorization secret>
```

### Full Runtime

Use Docker Compose or the Kubernetes/Helm manifests when you need the broader runtime:

```powershell
docker compose up --build
```

The full runtime is designed around:

- Flask web nodes
- PostgreSQL-ready configuration
- Redis worker runtime
- NATS JetStream event distribution
- Projection, replay, governance, notification, and worker execution surfaces

See `DEPLOYMENT.md` and `docs/` for more detail.

## Notes

- This is a software engineering and orchestration demo, not a medical device.
- Emergency flows are intentionally conservative and tell patients to seek immediate medical help instead of continuing normal scheduling.
- Production healthcare use would require clinical validation, privacy review, regulatory review, durable infrastructure, and provider-specific security controls.
