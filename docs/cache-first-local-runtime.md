# Cache-first local runtime

This branch is a narrow divergence from `v1.1.4` at
`4060ef5b11ffcec7b84a5d02446a9b82dbb38ff6`.

## Startup order

For Xiaomi-account config entries, startup now:

1. reads persisted device discovery without applying its cloud-refresh TTL;
2. validates model, IP address, 32-character hexadecimal token, and spec type;
3. accepts only models already present in `MIOT_LOCAL_MODELS`;
4. requires a persisted MIoT spec, then constructs local devices from cache;
5. prepares and authenticates the Xiaomi account best-effort;
6. refreshes normal cloud discovery when authentication succeeds.

Account verification, expired credentials, corrupt account auth storage, Xiaomi
API outages, and network failures no longer fail cached local devices. Cloud
message and scene-history sensors are created only while the account is ready.
No cloud fallback was added to local reads, writes, or actions.

The supported household model families are:

- `xiaomi.vacuum.c102gl`
- `zhimi.heater.mc2a`
- `xiaomi.airp.cpa4`
- `dmaker.fan.p33`
- `deerma.humidifier.jsq2w`

Other models are eligible only when upstream already lists them in
`MIOT_LOCAL_MODELS`. In particular, `yunmai.scales.ms104` remains cloud-only.

## Cache semantics

The existing TTL still controls cloud discovery and spec refresh. Complete
persisted local data may be used after its TTL during a cold start. Cache-only
spec loading never calls a Xiaomi endpoint; a missing or unusable spec makes
that device ineligible until a successful cloud setup can restore it.

Discovery refresh never replaces valid persisted data with an empty failed
response. This branch does not bundle account data, device tokens, or generated
production cache files.

## Compatibility and security

The integration domain, config-entry type, device identifiers, unique-ID
generation, entity construction, and local read/write/action dispatch are
unchanged from the pinned upstream commit and covered by source-contract tests.
`scripts/compatibility_manifest.py` snapshots and compares device-bound entity
registry metadata before and after a canary.

The `xiaomi_miot.get_token` service was removed. Account, config-flow, and setup
logging no longer serializes token-bearing config, auth responses, cookies, or
account payloads. Secrets must remain in Home Assistant config-entry/storage
data and must never be committed or placed in diagnostics.

## Known limits

- A local cold start requires the persisted discovery record and persisted
  parsed spec for each device.
- The existing account-style config entry, including its cache lookup metadata,
  must remain configured; this patch tolerates failed authentication but does
  not convert the entry into a token-only multi-device schema.
- An offline LAN device remains unavailable; cache-first startup does not mask
  physical reachability failures.
- Cloud-only entities remain unavailable while Xiaomi authentication is down.
- This branch has not been deployed to production. A production canary remains
  a separate approval gate.
