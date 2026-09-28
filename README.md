# DNS Migration Automation

Provider-neutral Python components for validating DNS zone data, finding IPAM networks, importing zones through an adapter, checking propagation, and restoring snapshots. The repository does not make live DNS changes by itself; connect a provider adapter and review a snapshot before running a migration.

## Requirements

- Python 3.10 or newer
- No third-party Python dependencies

## Zone file

Zone files are JSON documents with a zone name and a list of records. Each record has a relative or fully qualified `name`, a supported `type`, a positive integer `ttl`, and a string `value`.

```json
{
	"zone": "example.com",
	"records": [
		{"name": "www", "type": "A", "ttl": 300, "value": "192.0.2.10"}
	]
}
```

An example is in `config/zones/example.json`. Supported types are A, AAAA, CAA, CNAME, MX, NS, PTR, SRV, and TXT. A/AAAA address families and CNAME exclusivity are checked before import.

## Components

- `src/record_validator.py` validates and normalizes records.
- `src/ipam_lookup.py` selects the most-specific matching IPAM network.
- `src/zone_import.py` loads a zone and calls a `replace_zone` provider adapter only after validation succeeds.
- `src/propagation_checker.py` polls injected resolver adapters against expected answers.
- `src/rollback.py` writes validated JSON snapshots atomically and restores them through a provider adapter.

Provider adapters implement `replace_zone(zone, records)` for imports and rollback. Resolver adapters passed to the propagation checker implement `(name, type, resolver_label) -> iterable of answer strings`. Credentials and provider-specific behavior are intentionally left to the adapter implementation.

## Run checks

```sh
python -m compileall -q src tests
python -m unittest discover -s tests -v
```

GitHub Actions runs these checks on pushes and pull requests using Python 3.10, 3.11, and 3.12. The active workflow is `.github/workflows/ci.yaml`; `ci-cd/github-actions.yaml` is retained as the project-layout copy.

## Safety

Validate zone data and create a rollback snapshot before applying changes. Provider adapters should implement their own authentication, dry-run support, atomicity, audit logging, and safeguards against replacing an unintended zone. Never commit credentials or production zone exports.
