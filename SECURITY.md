# Security Policy

## Supported Surface

This repository contains public operational runtime code and support images.
Security fixes should preserve the public/private data boundary and should not
require access to private infrastructure.

## Reporting

Open a private security advisory or contact the maintainers through the
repository's configured security channel for vulnerabilities. Do not disclose
working exploits, private endpoints, credentials, secret identifiers, or live
operational facts in public issues or pull requests.

## Public Data Boundary

Public docs, fixtures, scripts, and examples must not contain:

- live hostnames, private domains, non-documentation IP addresses, or endpoint
  URLs;
- secret names, token values, project identifiers, credentials, or credential
  lookup commands;
- private deployment paths, private CI values, or operational runbook facts;
- copied private monitoring dashboards or generated evidence from live systems.

Use `example.com`, RFC 5737 IPv4 ranges, generated UUIDs, generic aliases, and
fake secret references for public examples.

## Release Boundary

Do not publish images, create tags, merge PRs, or publish a release without an
explicit maintainer approval gate. Validation and release-candidate inspection
may be automated; trusted publication is separate.
