# DNS Migration Automation

Provider-neutral Python tooling for planning and applying DNS zone migrations across Google Cloud DNS, Amazon Route 53, Azure DNS, and TCPWave REST endpoints. It validates zone data, diffs current and desired records, snapshots before writes, attempts automatic restoration after failed writes, checks resolver propagation, and supports read-only IPAM network lookup.

## Requirements

- Python 3.10 or newer
- Base dependencies: PyYAML and dnspython

Install the adapter(s) you need:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[aws,gcp,azure,tcpwave]'
```

Individual extras are available as `aws`, `gcp`, `azure`, and `tcpwave`. Tests inject fake SDK clients and do not need cloud credentials.

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

## Configure an environment

Add an environment mapping to `config/environments.yaml`. Keep secrets in environment variables and reference them as `${VARIABLE_NAME}`; `${VARIABLE_NAME:default}` supplies an explicit default.

```yaml
environments:
	production:
		provider: route53
		zone: example.com
		hosted_zone_id: Z0123456789EXAMPLE
		region: us-east-1
		ipam:
			provider: tcpwave
			base_url: https://ipam.example.net
			networks_path: api/ipam/networks
			token: ${TCPWAVE_TOKEN}
```

Provider-specific required settings:

- `route53`: `hosted_zone_id`; optional `region`.
- `cloud-dns`: `project_id`, `managed_zone`, and `zone`.
- `azure-dns`: `subscription_id`, `resource_group`, and `zone`.
- `tcpwave`: `base_url`, `list_path`, and `replace_path`; optionally `token`, `username`/`password`, `timeout`, and `verify_tls`.

For TCPWave IPAM, configure `ipam` with `provider: tcpwave`, `base_url`, and `networks_path`. The endpoint must return a JSON array of `{ "network": "CIDR", "metadata": {} }` objects, or an object with a `networks` array. DNS list endpoints return a flat `records` array; replacement endpoints accept `PUT { "zone": "...", "records": [...] }`. The endpoint paths are mandatory because TCPWave routes vary by installed version. See [provider adapters](docs/provider-adapters.md).

## CLI

```sh
dns-migrate validate config/zones/example.json
dns-migrate diff --environment production config/zones/example.json
dns-migrate apply --environment production --confirm config/zones/example.json
dns-migrate rollback --environment production --snapshot .snapshots/production-example.com-20260928T120000Z.json --confirm
dns-migrate check config/zones/example.json --resolver 1.1.1.1 --resolver 8.8.8.8
dns-migrate ipam-lookup 192.0.2.18 --inventory config/ipam-networks.json
```

`diff` is read-only. `apply` and `rollback` require `--confirm`. Before apply or rollback, the CLI writes the current target records to `.snapshots/`, which is excluded from Git. If apply fails, the CLI attempts to restore the pre-change snapshot and reports whether that recovery succeeded. `check` defaults to Cloudflare and Google public resolvers when no `--resolver` is supplied. `ipam-lookup` can use a JSON inventory or the environment's configured TCPWave endpoint. Unresolved environment references in optional settings are ignored until that provider is selected; references required by a selected provider cause a clear error.

## Components

- `src/record_validator.py` validates and normalizes records.
- `src/zone_diff.py` groups records into RRsets and computes additions, removals, and replacements.
- `src/providers/` contains Route 53, Cloud DNS, Azure DNS, and configurable TCPWave adapters.
- `src/ipam_lookup.py` selects the most-specific matching IPAM network.
- `src/propagation_checker.py` polls injected resolver adapters; `src/dns_resolver.py` binds those checks to dnspython.
- `src/rollback.py` writes validated snapshots atomically and restores them through an adapter.
- `src/cli.py` provides `validate`, `diff`, `apply`, `rollback`, `check`, and `ipam-lookup`.

Cloud providers use their standard credential chains: AWS SDK credentials, Google Application Default Credentials, and Azure `DefaultAzureCredential` (managed identity when hosted). TCPWave accepts a bearer token or basic-auth credentials from environment-variable references. The adapters do not log credentials.

## Run checks

```sh
python -m compileall -q src tests
python -m unittest discover -s tests -v
```

GitHub Actions installs the project and runs these checks on pushes and pull requests using Python 3.10, 3.11, and 3.12. The active workflow is `.github/workflows/ci.yaml`; `ci-cd/github-actions.yaml` is retained as the project-layout copy.

## Safety

The CLI requires explicit confirmation for writes and snapshots current records first, but this is not a substitute for a staging run or an approved change window. Route 53 alias/routing-policy records and provider-specific metadata are rejected rather than flattened; apex SOA and NS records are left to the DNS provider. See the documented adapter limitations before using an adapter against a production zone. Provider adapters must be granted least-privilege permissions. Never commit credentials or production zone exports.
