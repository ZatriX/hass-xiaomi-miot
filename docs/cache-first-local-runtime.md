# Cache-first local runtime

This branch is a narrow divergence from `v1.1.4` at
`4060ef5b11ffcec7b84a5d02446a9b82dbb38ff6`.

## Startup order

For Xiaomi-account config entries, startup now:

1. reads persisted device discovery without applying its cloud-refresh TTL;
2. validates model, IP address, 32-character hexadecimal token, and spec type;
3. accepts only models already present in `MIOT_LOCAL_MODELS`;
4. requires a persisted MIoT spec, then constructs local devices from cache;
5. forwards entity platforms and completes config-entry setup;
6. starts bounded Xiaomi authentication/discovery as a config-entry-owned
   background task.

Account verification, expired credentials, corrupt account auth storage, Xiaomi
API outages, and network failures no longer fail cached local devices. Cloud
message and scene-history sensors are created only while the account is ready.
No cloud fallback was added to local reads, writes, or actions.

The background bootstrap has a 45-second attempt timeout and retries after 60,
300, then 900 seconds. It is cancelled on config-entry unload, and starting a
replacement task cancels the previous one. Successful late discovery enriches
the existing runtime and creates only missing cloud/cloud-only entities through
the already registered platform adders; it does not rebuild cached local
devices or require a config-entry reload. An entry with no usable local cache
keeps the upstream synchronous cloud setup behavior instead of pretending that
a local runtime exists.

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

## Explicit local-only property write

The `xiaomi_miot.set_miot_property_local` action is a separate, fail-closed
primitive for controlled LAN write validation. It accepts an entity ID, exact
MIoT service ID (`siid`), property ID (`piid`), and value. The action requires
an initialized local transport whose most recent transport state is healthy,
then calls `device.local.async_send("set_properties", ...)` directly.

The action never invokes Xiaomi Cloud. A missing or unhealthy local transport,
a LAN exception, or an invalid device response returns an error without cloud
retry. Existing `set_property` and `set_miot_property` dispatch semantics are
unchanged; integrations and dashboards do not switch to the new action
implicitly.

Production physically validated both a same-value write and
`false -> true -> false` for Fyodor's indicator through this primitive. Each
write returned local device code 0, independent LAN reads confirmed the state,
and fan power, mode, level, swing and off-delay were unchanged.

### Physical canary result

For the available `dmaker.fan.p33` unit Fyodor, the cached MIoT spec exposes
`indicator-light.on` as service 4, property 1, boolean, with read/write/notify
access. It belongs to a dedicated indicator-light service and has no specified
relationship to fan mode, speed, swing, motor control, or timers. The physical
canary confirmed those related properties stayed unchanged for both same-value
and real-toggle writes. This result does not generalize to `fan.on` or other
writable properties.

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
- The cache-first lifecycle and explicit no-op discovery refresh passed their
  production canaries at commit `2627d382`.
- The explicit local-only write primitive is deployed and physically validated
  for Fyodor's indicator property only.
- The first HAOS-only network-blackhole canary on deployed commit `6cd554ba`
  exposed the blocking cloud await fixed by this follow-up. The decoupled cold
  start remains un-deployed until the repeat production canary succeeds.
- A physical new-device onboarding test remains deferred until a new Xiaomi
  device is actually available.
