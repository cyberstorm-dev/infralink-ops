# Stale Host And Controller Failure Triage

## BLUF

Start with read-only controller evidence, compare it to the approved Registry
revision, and then route the incident to the Registry, controller, or
application owner. Do not alter rendered configuration to make a symptom go
away. The goal is an evidence-backed owner decision, not a host-local repair.

## Capture Read-Only Evidence

Run these commands on the affected host. They inspect the launcher, timer, last
reconcile result, and recent controller service output:

```sh
infralink controller doctor
systemctl status infralink-host-reconcile.timer
systemctl status infralink-host-reconcile.service
sudo sed -n '1,220p' /var/lib/infralink/reconcile-result.yml
sudo journalctl -u infralink-host-reconcile.service --since '2 hours ago' --no-pager
```

Record the command exit status, the evidence timestamp, Registry revision and
ref, controller reference or digest, and the stable failure reason code. Do not
copy entire environment files or raw command environments into an issue.

## Decide The Owner

| Evidence | Owner | Safe next action |
| --- | --- | --- |
| The checked-out Registry revision differs from the approved promotion | Registry owner | Correct or re-promote the immutable Registry revision. |
| Registry evidence is valid but the controller cannot fetch, render, validate, or publish its reconcile evidence | Infralink Ops maintainer | Open a controller issue with the reason code, timestamps, immutable revision, and redacted logs. |
| Controller evidence is successful but the live service fails its acceptance check | Environment application owner | Investigate the application using its own runbook and acceptance evidence. |
| Host launcher, timer, or required host files are absent | Environment host-provisioning owner | Use the approved bootstrap/provisioning procedure; do not copy assets manually. |

`/var/lib/infralink/reconcile-result.yml` is the last controller result, not a
desired-state source. A successful file only means the controller completed its
declared work; it does not replace live-service acceptance.

## Safe Retry Boundary

If the Registry promotion is already approved and evidence indicates a transient
controller failure, use the existing one-shot unit:

```sh
sudo systemctl start infralink-host-reconcile.service
```

Collect `infralink controller doctor` again after the unit exits. Do not run ad hoc
Docker commands, check out a Registry revision inside the host working tree, or edit `/opt/services` directly. Those actions break the reconciliation evidence
chain and can conceal the source of drift.

Do not edit `/opt/services` directly to repair desired-state drift.

## Escalation Packet

Include only bounded, redacted evidence:

- host identifier and incident time window;
- approved Registry revision and the revision/ref recorded by the controller;
- controller image reference or digest and stable reason code;
- Doctor result and the relevant, redacted service-log lines; and
- the result of the environment-owned acceptance check, if applicable.

Do not disclose BWS tokens, private keys, or rendered secret values. Do not
attach `/etc/infralink/host.env`, raw Docker inspect output, or full secret
rendered files. Refer to the secret owner and the failure reason instead.

## Close The Loop

After the owner changes the authoritative source, repeat the normal Registry
promotion and controller reconciliation path. Confirm the new evidence and the
environment-owned acceptance result. A manual host workaround is not closure;
the next scheduled reconciliation must also converge cleanly.
