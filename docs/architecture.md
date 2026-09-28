# Architecture

The project separates migration policy from DNS/IPAM vendors. A normalized flat-record model is grouped into record sets and compared independently of the providers. Provider adapters own SDK-specific listing, serialization, reconciliation, and credential setup.

## Migration flow

1. `cli.py` loads an environment config and validates the JSON zone file.
2. `zone_diff.diff_zone` compares current provider records with the requested zone.
3. `apply` writes the current state to an atomic snapshot, then invokes provider reconciliation only after `--confirm`.
4. On apply failure, the CLI attempts to restore the saved state; `rollback` performs an explicit restoration and also creates a checkpoint first.
5. `propagation_checker.check_propagation` queries injected resolvers until expected answers appear or retries are exhausted.
6. `ipam_lookup.lookup_ip` returns the most-specific network, using either a local inventory or a read-only TCPWave IPAM REST endpoint.

## Adapter boundaries

DNS adapters implement `list_records`, `apply_diff`, and `replace_zone`. Route 53 submits grouped updates in transactional batches; Cloud DNS uses change sets; Azure DNS writes each record set through its management SDK; TCPWave uses explicitly configured list and full-replacement routes. Resolver adapters accept `(name, type, resolver_label)` and return answer strings. IPAM inventory can be represented by `IPAMNetwork` values or fetched from a configured read-only endpoint.

Cloud adapters use their standard SDK credential chains, with Azure `DefaultAzureCredential` supporting managed identity in hosted environments. TCPWave credentials are supplied from environment-expanded configuration and all TCPWave endpoints require HTTPS. Adapter-specific limitations are documented in `provider-adapters.md`. Writes remain opt-in in the CLI, but production deployments should add approval workflows and audit retention appropriate to the organization's change policy.
