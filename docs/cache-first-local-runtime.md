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

## Explicit cloud discovery refresh

New-device onboarding uses the existing Home Assistant-native
`xiaomi_miot.renew_devices` action, displayed as **Refresh devices from Xiaomi
cloud**. The action is scoped to one Xiaomi Miot account config entry through
the `config_entry_id` selector. The legacy `username` scope remains accepted for
backward compatibility, but an unscoped refresh is rejected. Home Assistant
restricts the action to administrators.

The operator flow is:

1. add and fully enrol the device in Xiaomi Home;
2. run **Refresh devices from Xiaomi cloud** for the account config entry;
3. allow the integration to authenticate and perform discovery without using
   the 24-hour discovery shortcut;
4. review the returned safe count summary;
5. let Home Assistant reload the config entry only when device facts changed.

The response contains `discovered`, `new`, `updated`, `unchanged`,
`local_capable_new`, `cloud_only_new`, `failed`, `new_models`, and `reloaded`.
It never contains tokens, credentials, cookies, or raw Xiaomi responses.

Refresh uses conservative merge semantics. Devices missing from one cloud
response remain in the persisted cache. Existing device identity facts are
retained, while changed IP addresses and tokens are accepted only after format
validation. A newly discovered model already present in `MIOT_LOCAL_MODELS`
must have a valid IP, 32-character hexadecimal token, MIoT spec type, and usable
spec cache before it is committed as local-capable. Other models remain on the
existing cloud-only path and are never added to `MIOT_LOCAL_MODELS`
automatically.

The model/type index and required spec/language payloads are refreshed and
validated first. Each HA Store write is atomic, and the merged device discovery
payload is committed last. Invalid candidates are skipped individually. An
authentication, discovery, or final cache-commit failure returns an operator
error, performs no config-entry reload, and leaves the loaded local runtime and
last known-good discovery cache in place. A successful no-op refresh also skips
reload, avoiding needless runtime interruption.

Successful discovery stores safe provenance metadata alongside the backward-
compatible cache payload: schema version, last successful refresh timestamp,
integration version, cache source, Xiaomi server, and account user ID scope.
Older cache payloads without this metadata remain readable.

For a future Xiaomi egress policy, onboarding is deliberately an operator
procedure rather than a startup dependency:

1. temporarily allow Xiaomi account, API, and MIoT-spec egress;
2. run the explicit refresh and verify its summary;
3. verify the new device after the conditional config-entry reload;
4. block Xiaomi egress again outside the integration.

The integration does not control DNS, routing, or firewall state.

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
- Include-style device filters can still exclude a newly discovered device;
  operators must deliberately update such filters before expecting entities.
- The cache-first lifecycle at commit `16ce8d3b` passed its production canary.
  The explicit refresh change remains un-deployed until its separate no-op
  production canary is reviewed and approved.
- A physical new-device onboarding test remains deferred until a new Xiaomi
  device is actually available.
