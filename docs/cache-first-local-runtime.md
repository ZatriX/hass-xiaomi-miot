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

## Operational status and cloud controls

The fork exposes current integration health through standard Home Assistant
surfaces without adding technical entities or additional device polling:

- **Settings -> Devices & services -> Xiaomi Miot -> Diagnostics** downloads a
  redacted config-entry report with runtime, cloud-bootstrap, cache provenance,
  and per-device transport/reachability facts;
- **Settings -> System -> Repairs -> System information** provides a quick
  Xiaomi Miot System Health summary built from the same existing runtime state;
- **Retry Xiaomi cloud connection** (`xiaomi_miot.retry_cloud`) requests an
  immediate config-entry-owned cloud bootstrap attempt;
- **Refresh devices from Xiaomi cloud** (`xiaomi_miot.renew_devices`) retains
  its conservative discovery/cache merge behavior and safe count response.

The runtime status is deterministic:

- `local_and_cloud`: a healthy local runtime, Xiaomi cloud ready, and no known
  cloud-only component failure;
- `local_only`: a healthy local runtime while Xiaomi cloud is unavailable;
- `cloud_only`: no configured local runtime, but the cloud path is ready;
- `degraded`: the integration remains usable but a known component is partial,
  such as cloud-ready local devices alongside unavailable cloud-only Glafira;
- `unavailable`: neither a usable local runtime nor a ready cloud path exists.

An individual long-offline local device such as Fedul is counted as unavailable
but does not by itself change a healthy integration to `degraded`. Glafira
(`yunmai.scales.ms104`) remains cloud-only; its missing cached spec or unavailable
cloud entity is reported as partial cloud degradation and never disables the
local household devices.

The cloud bootstrap state is `idle`, `running`, `backoff`, `ready`, or `stopped`.
Diagnostics includes attempt counts, safe success/failure timestamps, next
retry time, retry level, and only a normalized failure class (`auth_failed`,
`network_unreachable`, `timeout`, `discovery_failed`, `spec_failed`, `cancelled`,
or `unknown`). Exception messages and cloud response payloads are not retained.

Manual retry is administrator-only and scoped by `config_entry_id`. It never
reloads the integration or local devices. If bootstrap is running, it returns
`already_running`; during backoff it wakes the owned task for an immediate
attempt; when idle or completed it starts exactly one owned task. Its response
contains only `status`, `cloud_ready`, and `bootstrap_state`.

For normal household observation:

1. check Xiaomi Miot System Health for runtime status and local device counts;
2. download Diagnostics when cache provenance or retry timing is needed;
3. confirm local devices remain responsive in their existing dashboard cards;
4. use **Refresh devices from Xiaomi cloud** only for explicit discovery or
   onboarding;
5. use **Retry Xiaomi cloud connection** when cloud recovery should be attempted
   before the scheduled backoff expires.

No automation reloads or restarts the integration. Xiaomi egress remains
normally allowed; the HomeShield Xiaomi Canary profile is only for separately
approved controlled tests.

### Diagnostics and refresh acceptance procedure

Config-entry Diagnostics uses Home Assistant's standard administrator flow.
From an authenticated owner session, open **Settings -> Devices & services ->
Xiaomi Miot**, open the config-entry menu, and select **Download diagnostics**.
The frontend obtains a short-lived signed path and downloads
`/api/diagnostics/config_entry/<config_entry_id>`; the underlying endpoint is
administrator-only. Do not create a long-lived API token or weaken that check.

For a controlled discovery-refresh acceptance test, make exactly one call:

```yaml
action: xiaomi_miot.renew_devices
data:
  config_entry_id: <current config entry id>
```

Before pressing **Perform action**, record the wall-clock timestamp and the
`last_successful_refresh` cache-provenance value from a Diagnostics download.
Keep the Developer Tools Actions page open until it reports either **Response**
or an error. A successful response-capable call displays the complete safe
summary; absence of a response is a failed acceptance gate and is not a reason
to call the action again. Download Diagnostics once more afterward and verify
that `last_successful_refresh` advanced even when all discovered device records
were unchanged. Record the returned summary, whether `new` or `updated` is
non-zero, and whether `reloaded` is true. A no-op household refresh normally
reports `discovered: 8`, `new: 0`, `updated: 0`, `unchanged: 8`, and
`reloaded: false`; one failed cloud-only Glafira candidate is acceptable.

Inspect the downloaded JSON without copying secrets into logs or source files.
It must contain the safe runtime, cache, and cloud-bootstrap sections and must
not contain local device tokens, Xiaomi password/auth data, cookies,
`ssecurity`, account identifiers, or raw Xiaomi responses.

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
- Cache-first cold start, bounded cancellation, local reads and writes, explicit
  refresh, and background cloud/coordinator recovery have been physically
  validated through commit `484fa177`.
- Operational diagnostics and cloud controls are not production-validated until
  their separate rollout gate completes.
- A physical new-device onboarding test remains deferred until a new Xiaomi
  device is actually available.
