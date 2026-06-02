# DOCQ Production Deployment Guide

## Required services

- PostgreSQL
- NATS JetStream
- Redis
- DOCQ web nodes
- DOCQ worker nodes

## Startup order

1. PostgreSQL readiness
2. NATS JetStream readiness
3. Redis readiness
4. DOCQ web deployment
5. Worker deployment
6. Projection / governance / replay workers

## Health endpoints

- `/health`
- `/ready`
- `/metrics`
- `/api/observability/topology`
- `/api/deployment/validate`

## Release safety

- Rolling deployment with zero unavailable pods.
- Readiness/liveness probes gate traffic.
- Deterministic startup dependency validation should pass before production cutover.
