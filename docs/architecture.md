# DOCQ Distributed Runtime Architecture

DOCQ is a deterministic orchestration platform built around canonical workflow events, replay-safe governance, and distributed operational automation.

## Core runtime

- Canonical event store is the replay authority.
- Transactional outbox publishes committed events to the distributed event bus.
- CQRS projections derive read models for dashboards, observability, governance, and analytics.
- Replay snapshots and distributed replay workers accelerate deterministic reconstruction.

## Distributed topology

- PostgreSQL: durable source of truth and replay authority.
- NATS JetStream: event distribution backbone.
- Redis / worker runtime: distributed execution and retries.
- Projection workers: projection rebuild and operational read acceleration.
- Governance workers: evaluation, drift, recommendation, and rollout simulation.

## Invariants

- Append-only lineage.
- Deterministic replay semantics.
- Tenant-scoped isolation.
- Replay-safe governance and operational automation.
