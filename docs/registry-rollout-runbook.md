# Registry Promotion, Reconciliation, And Rollback

## BLUF

Promote one reviewed immutable Registry revision, let the installed controller
reconcile it, and verify the resulting host evidence. To roll back, select the
previous reviewed immutable Registry revision and repeat the same path. Registry is the sole desired-state authority. Do not edit Compose files or rendered configuration on the host.

## Promote One Immutable Registry Revision

1. Make and review the declared change in the environment's Registry
   repository.
2. Complete that Registry's validation and promotion procedure.
3. Record the selected immutable revision in the environment's promotion
   evidence.
4. Wait for the target host's `infralink-host-reconcile.timer`, or use the
   environment-approved one-shot service retry when the rollout is time
   sensitive.

The controller consumes the Registry ref configured for the host. It does not
choose a branch, create a promotion record, or infer a replacement revision.
The target checkout at `/var/lib/infralink/registry` is controller working
state, not an operator editing surface.

## Verify Reconciliation Evidence

First collect the controller's read-only evidence:

```sh
infralink-host doctor
systemctl status infralink-host-reconcile.service
```

Then inspect the persisted result:

```sh
sudo sed -n '1,220p' /var/lib/infralink/reconcile-result.yml
```

A successful result records the host UUID, Registry revision and ref,
controller reference and digest, adapter evidence, and observation time. Match
the recorded Registry revision to the promotion evidence. Finally run the
environment-owned live-service checks; controller success does not prove an
application is accepting traffic correctly.

If reconciliation fails, retain the returned reason code and evidence. Use the
[controller runtime triage guide](controller-runtime-guide.md#triage-a-stale-host)
to determine whether the fault belongs to Registry intent, controller behavior,
or an environment-owned application check.

## Roll Back By Selecting A Prior Revision

1. Identify the last known-good immutable Registry revision from its promotion
   and acceptance evidence.
2. Use the Registry's normal promotion mechanism to select that prior revision.
3. Let the same controller path reconcile it on the target host.
4. Verify `infralink-host doctor`, the persisted reconcile evidence, and the
   environment-owned acceptance checks again.

Rollback is not a host-local `git checkout`, Docker Compose edit, or a manual
rewrite of `/opt/services/config`. Those actions create drift that the next
reconcile must undo and destroy the evidence link between Registry intent and
host state.

## When To Escalate

Escalate to the Registry owner when the selected revision is wrong, missing, or
fails Registry validation. Escalate to this repository when the controller
cannot consume a valid selected revision or emits invalid evidence. Escalate to
the environment application owner when controller evidence is correct but the
live-service acceptance check fails.

For an unbootstrapped host, do not use this runbook as a provisioning shortcut.
Use the environment's approved host bootstrap procedure. The controller
runtime guide documents the bounded primitives behind this process; it does not
replace Registry promotion authority.
