# Milestone 7 — Hardening

**Status:** done

## Authentication

One shared key from `GAIA_API_KEY`, checked against the `x-gaia-key` header. v0.1 has no
user model by design; this is a deployment boundary, not an identity system.

With no key set the API is open **and says so on startup**. Silently running unauthenticated
while looking configured is worse than either alternative.

`/health` is exempt, so a load balancer can still see the process.

## Rate limiting

Fixed-window counter, 120 requests per minute per client address, `GAIA_RATE_LIMIT` to
change it. In-memory, which is the right size for one process and one shared key — a
distributed limiter needs a store this deployment does not have, and adding one now would
be speculative infrastructure.

`/health` is exempt here too: a load balancer should pull a node for being broken, not for
being busy. Seven tests cover the limit, the headers, the retry-after, address separation,
and the forwarded-chain case.

## Error envelopes

Every failure has a stable code an agent can branch on without parsing prose:
`aoi_not_ingested`, `no_data_for_period`, `claim_not_found`, `lake_unavailable`,
`indicator_unavailable`, `invalid_request`, `rate_limited`, `internal`. Each carries a
message, an optional detail with the command that would fix it, and a `retryable` flag.

Unknown routes return the same shape rather than an HTML 404.

## The provenance guard

Runs in three places:

1. **Schema.** The envelope cannot be constructed without a provenance chain, a validation
   status, a confidence and a method. `RejectedValue` has no `value` field at all. 28 tests
   in `test_provenance_guard.py` assert these are unconstructible rather than merely absent.
2. **Service layer.** Every response is walked structurally before it is returned.
3. **API edge.** Walked again as JSON leaves the process.

Disabled only by `GAIA_STRICT_GUARD=0`. The runbook says not to.

## Cold start

`make setup && make seed && make dev`. No accounts, no credentials, no keys — every source
v0.1 uses is open and anonymous.

Setup is about two minutes. The seed is the long pole at 45 to 60 minutes for twelve
months, measured rather than estimated, with two documented ways to shorten it. The RUNBOOK
originally claimed 10 to 20; that was a guess and it was wrong, and it has been corrected.

## Known limitations, stated

- An ingest holds the lake's write lock, so the API cannot read during a seed (D-005).
- Climate comes through Open-Meteo rather than the CDS (D-002).
- TWI is computed at 100 m (D-006).
- The whitepaper this was built against is a placeholder (D-001).
