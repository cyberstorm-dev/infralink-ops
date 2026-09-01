# Controller Runtime CLI Reference

## BLUF

Operators normally use `infralink` for bounded inspection and `infralink-host`
for the installed host interface. Controller primitives are private runtime
modules: they receive explicit inputs from the configured runtime and should
not be used as an alternate desired-state workflow. The Registry
repository remains authoritative for topology, image selection, and promotion.

## Operator Entry Points

| Command | Lifecycle phase | Mutability | Output | Owner |
| --- | --- | --- | --- | --- |
| `infralink` | Inspect declared topology, observation, and readiness evidence | Read-only unless an explicit public CLI write/apply gate is supplied | Structured CLI envelope | Public `infralink` package |
| `infralink-host doctor` | Diagnose a managed host | Read-only | Controller evidence | Installed host interface |
| `infralink-host reconcile` | Timer-driven host reconciliation | Changes controller-owned runtime state | Reconcile result and metrics | Installed host interface |
| `infralink-host bootstrap --apply` | Initial host interface provisioning | Writes host interface and enables the reconcile timer | Bootstrap envelope | Environment host-provisioning procedure |

`infralink` is run through the installed host wrapper with the configured
Registry checkout mounted read-only. It is useful for bounded queries and
machine-readable results, but it is not a desired-state selector.

Use the host timer or its one-shot systemd service for an approved reconcile.
Do not invoke controller primitives manually to compensate for a missing
Registry promotion or to patch a running service.

## Controller-Internal Primitives

The controller composes these commands during reconciliation. Their input and
output contracts are intentionally narrow; the table identifies their owner,
not a new operator workflow.

| Primitive | Lifecycle role | Mutates host state? | Input authority |
| --- | --- | --- | --- |
| Controller-owned Registry checkout | Fetch a configured Registry ref into the sole clean checkout | Yes, controller checkout only | Host runtime configuration and Registry transport contract |
| Controller-owned template rendering | Render declared templates | Yes, declared rendered target only | Registry declarations and controller-provided values |
| Config-consumer activation | Validate or activate affected Compose consumers | Yes | Rendered controller-owned config change set |
| Controller-owned secret rendering | Resolve declared BWS render bindings | Yes, rendered target only | Registry-declared binding and runtime BWS access |
| Controller-owned firewall stage | Render or verify declared firewall policy | Render is local; apply behavior is controller-owned | Registry firewall declaration |
| `infralink controller doctor` | Collect host consistency evidence | No | Controller runtime evidence paths |
| Controller-owned bootstrap stage | Materialize host launcher and timer assets | Yes, explicit apply only | Approved bootstrap request |

Supporting commands such as image resolution, image retention, runtime
directories, artifact bindings, transport trust, protected transitions,
reconcile evidence, and metrics are also controller internals. They preserve
explicit contracts for the runtime; they do not move policy out of Registry.

## Ownership And Mutability

| Question | Authority |
| --- | --- |
| Which revision, profile, template, image, hostname, or service should run? | Registry repository |
| How does the host fetch, render, validate, activate, and record evidence? | Infralink Ops controller runtime |
| What does a portable topology or CLI result mean? | Public Infralink package |
| Does a live service meet its user-facing acceptance criteria? | Environment application owner |

Commands that touch BWS, Docker, systemd, or rendered configuration do not
grant operators ownership of the underlying policy. Do not pass undeclared
secret identifiers, edit `/opt/services` directly, or turn a controller helper
into an ad hoc deployment mechanism.

## Reading Structured Results

Treat stdout as the command contract and retain the bounded result envelope or
reconcile evidence in the incident record. Keep pull progress, system logs, and
other diagnostic streams separate from structured output. Stable reason codes,
immutable Registry revisions, controller image references, and timestamps are
useful escalation evidence; tokens and rendered secret values are not.

For the normal operator sequence, see the [installed CLI quickstart](installed-cli-quickstart.md).
For failed or stale reconciliation, use the [controller runtime guide](controller-runtime-guide.md#triage-a-stale-host).
