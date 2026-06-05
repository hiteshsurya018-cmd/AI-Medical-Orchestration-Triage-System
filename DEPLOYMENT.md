# DOCQ Deployment

## Vercel Flask Deployment

Vercel can run this project as a Flask serverless function through `app.py`. It cannot run the Docker Compose stack directly, and its function filesystem is read-only except for `/tmp`, so the bundled SQLite mode is only suitable for demo or preview deployments.

Required Vercel environment variables:

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
CRON_SECRET=<random string for Vercel Cron authorization>
```

For a Vercel demo that boots without persistent storage, leave `DOCQ_DATABASE_URL` unset. The app will use `sqlite:////tmp/docq.db` on Vercel.

For production persistence, add a managed database and set:

```text
DOCQ_DATABASE_URL=<managed database url>
DOCQ_DB_PATH=/tmp/docq.db
```

The current application schema initializer is SQLite-oriented, so a production Postgres deployment needs a full table-creation migration before `DOCQ_DATABASE_URL=postgresql://...` will be reliable.

Appointment notifications also require the same provider secrets you use locally:

```text
SMTP_HOST=<smtp host>
SMTP_PORT=<smtp port>
SMTP_USERNAME=<smtp username>
SMTP_PASSWORD=<smtp password>
SMTP_FROM=<sender address>
TWILIO_ACCOUNT_SID=<twilio account sid>
TWILIO_AUTH_TOKEN=<twilio auth token>
TWILIO_FROM_NUMBER=<sms sender>
TWILIO_WHATSAPP_FROM=<whatsapp sender>
DOCQ_N8N_CONFIRMATION_WEBHOOK=<optional confirmation webhook>
```

Vercel invokes `/api/cron/reminders` daily from the `vercel.json` cron entry. The endpoint queues reminders for tomorrow's appointments and processes queued notification deliveries.

Deploy with Vercel CLI:

```powershell
npm i -g vercel
vercel login
vercel link
vercel env add DOCQ_SECRET_KEY production
vercel env add DOCQ_JWT_SECRET production
vercel env add DOCQ_PII_ENCRYPTION_KEY production
vercel deploy --prod
```

## Full Feature Deployment

Use Docker Compose when you need all backend features at once:

- Flask web app
- worker runtime
- Redis
- NATS JetStream
- Postgres service
- persistent volumes
- healthchecks

```powershell
docker compose up --build
```

Vercel does not deploy Docker images or run Docker Compose services. For a single-provider full deployment, use a container host that supports persistent volumes and sidecar services.
