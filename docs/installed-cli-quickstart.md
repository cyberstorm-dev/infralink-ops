# Installed Infralink CLI Quickstart

## BLUF

Start with read-only evidence. Use `infralink controller doctor` to learn whether the
installed controller agrees with its configured Registry input, then inspect
the systemd timer and the last reconcile evidence. The launcher returns
machine-readable evidence from the controller; it is not a second place to
select desired state or edit service configuration.

## Before You Run Anything

Run these commands on the managed host with the operator access required by
your environment. The installed launcher reads `/etc/infralink/host.env`, which
defines the controller seed image and Registry connection inputs. Do not print
or copy that file into tickets: it can contain private runtime references.

Confirm the launcher is present, then collect read-only context:

```sh
command -v infralink
systemctl status infralink-host-reconcile.timer
```

If the launcher or timer is absent, stop. The host has not completed the
environment-owned bootstrap path; use that environment's host provisioning
procedure rather than manually copying launcher files.

## Read-Only Host Evidence

Run Doctor before retrying a reconcile or changing any configuration:

```sh
infralink controller doctor
```

Doctor checks the controller's declared Registry, reconcile, Compose, firewall,
and metrics evidence without selecting a Registry revision or activating a
service. Save the returned machine-readable evidence with the incident record.
Then inspect the last persisted result only as evidence, not as desired state:

```sh
sudo sed -n '1,220p' /var/lib/infralink/reconcile-result.yml
```

The expected Registry checkout is `/var/lib/infralink/registry`. If it does not
match the environment's approved revision, fix the Registry selection or its
promotion path. Do not repair the checkout by hand.

## Reconcile Only Through The Timer

Normal reconciliation is scheduled by `infralink-host-reconcile.timer`. Check
when it last ran and whether its service completed:

```sh
systemctl status infralink-host-reconcile.timer
systemctl status infralink-host-reconcile.service
```

When an approved Registry change needs an immediate retry, start the existing
one-shot unit rather than invoking the launcher directly:

```sh
sudo systemctl start infralink-host-reconcile.service
```

Re-run `infralink controller doctor` after the unit completes. If the evidence still
fails, follow the [controller runtime triage guide](controller-runtime-guide.md#triage-a-stale-host)
and change the owning source rather than patching runtime output. Do not edit `/opt/services` directly.

## Bootstrap Is An Explicit Host Change

The canonical `infralink host bootstrap` operation writes the host interface
and enables the reconcile timer. It requires an explicit `--apply` gate and is
not part of ordinary diagnosis or rollout recovery. Run it from the approved
control environment, not through a host-local shim:

```sh
infralink help host bootstrap
```

Use bootstrap only through an approved host-provisioning change. Before using
it, ensure no other reconciliation timer or legacy launcher is active. The
controller rejects conflicting timer ownership rather than silently replacing
another host controller.

## Boundary And Next Step

This installed launcher owns host-local controller invocation. It does not own
the topology, image selection, DNS, tenant policy, secret aliases, or the
acceptance criteria for a live application. Those are Registry or
environment-owned concerns. The [controller runtime guide](controller-runtime-guide.md)
maps the controller primitives and evidence surfaces; the public
`cyberstorm-dev/infralink` repository defines the portable CLI and schema
contracts.
