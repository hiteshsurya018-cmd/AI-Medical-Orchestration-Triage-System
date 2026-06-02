# DOCQ Integration Architecture

Integration adapters remain subordinate to canonical event authority.

## Supported adapters

- Twilio SMS
- SMTP / SendGrid-style email
- Google Calendar OAuth scaffolding
- Outlook Calendar OAuth scaffolding
- Slack webhooks
- generic webhooks

## Delivery invariants

- integration actions derive from canonical workflows
- health is recorded in `integration_health`
- failures remain deterministic and auditable
- no integration can become replay authority
