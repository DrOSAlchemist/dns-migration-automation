# Architecture

The project separates migration policy from DNS/IPAM vendors. Core functions use standard-library data structures, and provider-specific network operations enter through small injected adapter contracts.

## Migration flow

1. `zone_import.load_zone` reads JSON and delegates all records to `record_validator.validate_records`.
2. The importer calls `replace_zone` only after the complete zone passes validation.
3. `ipam_lookup.lookup_ip` returns the most-specific IPAM network for an address.
4. `propagation_checker.check_propagation` queries injected resolvers until expected answers appear or retries are exhausted.
5. `rollback.save_snapshot` writes a validated pre-change zone atomically; `rollback.rollback` validates it again before invoking the provider.

## Adapter boundaries

DNS adapters implement `replace_zone(zone, records)`. The method must provide provider-appropriate authorization checks and should make replacement atomic where supported. Resolver adapters accept `(name, type, resolver_label)` and return answer strings. IPAM inventory can be represented by `IPAMNetwork` values or parsed from mappings.

No module performs live network calls or reads credentials. Deployment-specific code should supply adapters and secrets through its runtime environment, add dry-run and audit behavior, and require explicit confirmation before production writes.
