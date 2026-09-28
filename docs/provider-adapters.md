# Provider Adapters

The adapters reconcile the project's flat record schema with provider record sets. Optional SDK packages are isolated in `pyproject.toml` extras. Tests use injected clients and do not call provider APIs.

## Cloudflare DNS

No optional package is required. Configure `provider: cloudflare`, `zone`, `zone_id`, and `api_token`; use an environment reference such as `${CLOUDFLARE_API_TOKEN}` for the token. Create a Cloudflare API token scoped to the sandbox zone with DNS Read and DNS Write permissions. Listing is paginated automatically, and apex SOA and NS records remain provider-managed.

The adapter supports the project's standard record types when they are unproxied and have no Cloudflare-only comments, tags, settings, private-routing, or glue metadata. Such records are rejected rather than silently losing behavior. Changes are sent as individual delete/create API calls, not an atomic zone change set; a failed request can leave a partially applied diff, so use the CLI snapshot and verify its best-effort rollback before relying on it.

References: [Cloudflare DNS records API](https://developers.cloudflare.com/api/resources/dns/subresources/records/) and [API token setup](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/).

## AWS Route 53

Install with `pip install -e '.[aws]'`. Configure `provider: route53`, `hosted_zone_id`, and optional `region`. boto3 uses its standard credential chain, such as an AWS profile locally or an IAM role in a hosted runner. Grant only the needed Route 53 list/change permissions for the target hosted zone.

The adapter groups values with the same owner/type into a Route 53 record set and submits changes in batches of at most 1,000. SOA and apex NS records are provider-managed. Alias records and records with routing-policy identifiers are rejected during listing so reconciliation cannot overwrite metadata it does not model.

Reference: [Boto3 `change_resource_record_sets`](https://docs.aws.amazon.com/boto3/latest/reference/services/route53/client/change_resource_record_sets.html).

## Google Cloud DNS

Install with `pip install -e '.[gcp]'`. Configure `provider: cloud-dns`, `project_id`, `managed_zone`, and `zone`. The Google client uses Application Default Credentials. Grant the runtime identity the narrow Cloud DNS permissions required to list and change the managed zone.

The adapter reads resource record sets and submits a Cloud DNS change set. SOA and apex NS records are provider-managed. Standard records that cannot be represented by the project's record format must not be flattened by custom extensions.

Reference: [Google Cloud DNS Python client](https://docs.cloud.google.com/python/docs/reference/dns/latest) and [Cloud DNS change sets](https://docs.cloud.google.com/python/docs/reference/dns/latest/changes).

## Azure DNS

Install with `pip install -e '.[azure]'`. Configure `provider: azure-dns`, `subscription_id`, `resource_group`, and `zone`. The adapter uses `DefaultAzureCredential`; use managed identity in Azure-hosted environments and grant that identity least-privilege DNS Zone Contributor access scoped to the target zone or resource group. Local development may use an authenticated Azure CLI identity.

This adapter targets public Azure DNS zones through `azure-mgmt-dns`. It supports A, AAAA, CAA, CNAME, MX, NS, PTR, SRV, and TXT record sets. Azure DNS Private Resolver/private-zone management uses different APIs and is not implemented here. SOA and apex NS records are provider-managed.

Reference: [Azure DNS `RecordSetsOperations`](https://learn.microsoft.com/en-us/python/api/azure-mgmt-dns/azure.mgmt.dns.operations.recordsetsoperations).

## TCPWave DNS and IPAM

TCPWave routes and payloads vary by product version and installation, so this adapter deliberately does not invent a default API path. Configure `base_url`, `list_path`, and `replace_path` explicitly. The base URL must use HTTPS. Authentication can use `token` (Bearer) or `username` and `password`; put secret values in environment variables and reference them in YAML.

The configured DNS list route must return either a flat JSON array or `{ "records": [...] }`, with each record using `name`, `type`, `ttl`, and `value`. The replacement route must accept `PUT` JSON `{ "zone": "example.com", "records": [...] }` and replace the managed records for that zone. Confirm this contract against the deployed TCPWave API version before enabling writes. GET retries use exponential backoff; PUT is not automatically retried.

For IPAM, configure `ipam.provider: tcpwave`, `ipam.base_url`, and `ipam.networks_path`. The read-only endpoint must return `{ "networks": [{ "network": "192.0.2.0/24", "metadata": {} }] }` or a bare array with those entries. The tool returns the longest-prefix match.

## Shared Behavior and Limits

- Supported project record types are A, AAAA, CAA, CNAME, MX, NS, PTR, SRV, and TXT.
- Values are one text RDATA value per record object; multiple values in an RRset are separate objects with the same name, type, and TTL.
- One RRset cannot have multiple TTL values. Owner/type RRsets are replaced as a unit.
- Apex SOA and NS records are left untouched. Advanced routing, alias, traffic-policy, and provider-only metadata must be modeled before migrating zones that use them.
- CLI apply and rollback require `--confirm` and create a pre-operation snapshot. Provider-level transactional behavior differs; automatic rollback is best-effort and must be verified.
- Propagation checking queries each configured nameserver for every expected record value. It does not prove that every recursive resolver worldwide has expired cached data.