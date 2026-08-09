# Control plane

Hermes Kanban board `untether-local` is the durable control plane for owner-requested local changes. Codex implements bounded cards; Hermes reviews, verifies, installs, and performs live service checks.

No push, publication, upstream PR, secret access, production install, or service restart unless explicitly authorised by the owner. The owner authorised local implementation, installation, and verification of Telegram reply/quote support on 2026-08-09.

Every implementation handoff must include changed files, verification commands/results, residual risks, and a review-required marker. `scripts/verify` is the canonical deterministic gate.
