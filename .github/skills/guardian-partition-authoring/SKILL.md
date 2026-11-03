---
name: guardian-partition-authoring
description: "Use when creating or changing Guardian partitions, intents, or assets (Compute, ImageBuild, Volume, Config, Secret, LoadBalancer, Network, ObjectStore, Database, CDKStack, Observability), validating a partition bundle, or wiring images, secrets, and volumes into a deployment."
---

# Author Guardian partitions, intents, and assets

Use this skill when the user asks to create a partition, add an intent, add an
asset, scaffold a deployable service, or change a Guardian bundle.

## Goal

Create or modify a valid Guardian bundle that can be pushed, compiled, and
reconciled without hand-waving. Prefer existing examples and the live asset
catalog over memory.

## Model

`Partition -> Intent -> Asset`.

- **Partition** — top-level grouping with reconciliation settings, defaults,
  labels, and deletion policy.
- **Intent** — a set of assets deployed together. `joins` gate the whole intent
  on other intents; `targetPusher` / `target` choose where it runs.
- **Asset** — one resource (`Compute`, `ImageBuild`, `Volume`, `Config`,
  `Secret`, `LoadBalancer`, `TraefikRoute`, `Network`, `ObjectStore`,
  `Database`, `SQLDatabase`, `CDKStack`, `Observability`, ...).
- `dependsOn` orders assets inside an intent. `joins` orders intents.
- `locked: true` stops drift from being applied automatically.
- Outputs flow via `${intent.<intent>.outputs.<key>}`. `ImageBuild` outputs
  `imageRef`; APPLY also exposes `<asset>.<key>` namespaced keys.

## Before writing

1. Read two or three existing bundles:
   - `stratatools/partitions/agent` — multi-asset app, volumes, image builds,
     secrets
   - `stratatools/partitions/doctor` — multiple intents, secrets, rules
   - `stratatools/partitions/monitoring` — Config + Compute
   - `stratatools/partitions/_bootstrap` — bootstrap image builds
2. Inspect the live catalog and target:
   ```bash
   guardianctl -format json asset catalog
   guardianctl -format json asset describe --type Compute
   guardianctl -format json partition list
   guardianctl -format json intent list --partition <partition>
   ```
   When running inside the agent, prefer the native tools:
   `guardian_catalog`, `guardian_asset_describe`,
   `guardian_partition_validate`, and `guardian_intent_plan`.
3. Find the intended pusher, cluster, and namespace. Do not invent them.
   - inspect existing intents for `targetPusher` and `target`
   - check `kubectl get pods -n guardian`
   - check `guardianctl -format json partition get --partition <existing>`
4. If scaffolding from a Dockerfile or source directory, use the
   `create-deployable-partition` skill first.

## Bundle layout

Repository-managed bundle:

```text
guardian/partitions/<partition>/
  partition.yaml
  intents/<intent>.yaml
  payloads/<asset>.k8s.yaml     # optional raw Kubernetes payloads
  templates/                    # optional CDK/template overlays
  secrets/                      # never commit; local/MonoFS only
```

`stratatools/partitions/<partition>/` uses `config.yaml` instead of
`partition.yaml`; follow the directory you are working in.

## Partition manifest

```yaml
apiVersion: guardian/v1alpha1
kind: Partition
metadata:
  name: example
  labels:
    role: app
spec:
  defaults:
    observability:
      enabled: true
      autoInstrument: otelc
      cloudwatch: auto
    probes:
      enabled: true
      interval: 30s
      timeout: 3s
      retries: 2
      httpPaths: ["/healthz", "/health", "/ready", "/"]
      tcpPorts: [80, 443, 8080]
      maxTargets: 200
      allowExternal: false
  deletionPolicy: orphan        # orphan | destroy
  reconciliation:
    mode: auto                  # auto | manual | readonly
    interval: 1m
  labels:
    stack: k8s
    topology: kubernetes
    managedBy: guardian
```

Rules:

- `metadata.name` and all intent/asset names must match
  `^[a-z][a-z0-9-]*$`.
- Use `deletionPolicy: orphan` unless the user explicitly wants destroy
  semantics.
- Use `reconciliation.mode: manual` for sensitive partitions.
- Never put secrets in labels.

## Intent manifest

```yaml
apiVersion: guardian/v1alpha1
kind: Intent
metadata:
  name: app
spec:
  intentType: standard
  joins:
    - images
    - secrets
  targetPusher: k8s-main
  target:
    cluster: k8s-main
    namespace: example
  locked: false
  assets:
    - type: Config
      name: app-config
      version: "1"
      properties:
        data:
          app.yaml: |
            port: 8080
        format: yaml

    - type: Volume
      name: app-data
      version: "1"
      properties:
        accessMode: ReadWriteOnce
        size: 5Gi

    - type: Compute
      name: app
      version: "1"
      dependsOn:
        - app-config
        - app-data
      payload:
        k8s: /partitions/example/payloads/app.k8s.yaml
      properties:
        image: ${intent.images.outputs.app-build.imageRef}
        imagePullPolicy: IfNotPresent
        ports:
          - containerPort: 8080
            name: http
            protocol: TCP
            servicePort: 8080
        env:
          APP_CONFIG: /etc/app/app.yaml
        configMounts:
          - config: app-config
            path: /etc/app/app.yaml
            readOnly: true
        volumeMounts:
          - path: /data
            volume: app-data
        resources:
          requests:
            cpu: 100m
            memory: 128Mi
          limits:
            cpu: "1"
            memory: 512Mi
```

Rules:

- Every asset needs `type`, `name`, and `version`.
- Use `dependsOn` for assets that must exist first; use `joins` for whole
  intents.
- Keep `properties` small and readable. Put large or raw Kubernetes YAML in
  `payloads/` and reference it via `payload.k8s`.
- `target.namespace` must already exist or be created by the pusher; do not
  assume it exists.
- Use `locked: true` when drift must never auto-apply.

## Common asset patterns

### ImageBuild

```yaml
- type: ImageBuild
  name: app-build
  version: "1"
  properties:
    buildContext: ~/strata/app
    dockerfile: Dockerfile
    registry: registry.strata.local:5000
    repository: app
    target: runtime              # optional Docker stage
    buildArgs:
      BASE_IMAGE: ...
```

Consume the output with
`image: ${intent.images.outputs.app-build.imageRef}`.

### Compute

Required: `image`. Common fields: `ports`, `env`, `resources`, `replicas`,
`command`, `args`, `imagePullPolicy`, `healthCheck`, `volumeMounts`,
`configMounts`, `capabilities`, `privileged`.

Ports need `containerPort`; set `servicePort` when the pusher should create a
Service.

### Volume

`size` and `accessMode` (`ReadWriteOnce`, `ReadWriteMany`, `ReadOnlyMany`).
Reference from `volumeMounts`.

### Config

Inline `data` (map of filename to content), inline `content`, or a payload.
Prefer the pusher-agnostic payload keys:

```yaml
payload:
  file: /partitions/<p>/payloads/prometheus.yml   # single raw file
```

`payload.file` (alias `raw`) points at any normal file and uses its basename
as the rendered filename. `payload.files` points at a YAML document with a
`files:` map for multi-file configs. Legacy `payload.k8s` / `payload.docker` /
`payload.aws` keys still work and use a `data:` (Kubernetes) or `files:` map.

Mount with `configMounts`. When the payload has one file, its name is used as
`subPath` when the mount path basename matches; use `configMounts[].file` to
select a file explicitly instead of adding an inline stub. Formats:
`text|yaml|json|binary` (binary properties are base64; raw payload files carry
bytes directly).

### Secret

Use `secretRef` or `tokenEnv`; never inline secret values in a repo-managed
partition. Reference secrets at runtime through the provider's secret
placeholders.

The agent partition's SSO client secret is a concrete example:

```yaml
- type: Secret
  name: agent-oidc-client-secret
  version: "1"
  properties:
    secretRef: monofs-secret://shared/agent-oidc-client-secret
```

Seed the MonoFS secret before releasing the consuming intent:

```bash
guardianctl partition push --dir stratatools/partitions/shared \
  --include-secrets --remove-missing=false
```

### Network, LoadBalancer, TraefikRoute

Read `guardianctl asset describe --type <Type>` for the exact schema. Do not
guess listener or protocol fields.

### Database, SQLDatabase, ObjectStore, CDKStack

These are AWS-oriented assets. Use `guardianctl asset describe --type <Type>`
and `guardianctl template list` before writing. They may require a `Network`
asset and account/region configuration.

### Observability

Use the partition defaults for auto-instrumentation. Add an `Observability`
asset only when a service needs a non-default collector or sidecar.

## Interpolation and outputs

- `${intent.<intent>.outputs.<key>}` references a known intent output.
- `${intent.<intent>.outputs.<asset>.<key>}` references a namespaced asset
  output.
- Unresolved placeholders are preserved at compile time, so upstream intents
  can apply first.
- Inspect available outputs with
  `guardianctl -format json intent outputs --partition <p> --intent <i>`.

## Places to be careful

- Do not copy `K8sResource` assets blindly. The current local validator may
  reject them even though some existing bundles use them (for example
  `k8s-top` and `opentelemetry`). Prefer a catalog-backed type, or verify
  runtime support with the pusher.
- Do not commit `secrets/` or inline `Secret.value`.
- Do not invent ports, images, or namespaces. Inspect the Dockerfile, service,
  or ask the user.
- Do not deploy to a shared or production partition without explicit approval.
- Keep partition state (`.state/`) and generated files out of manual edits.

## Validation

```bash
# Parse and validate every bundle under a partitions root
guardianctl partition validate-local --root ./stratatools/partitions

# Validate one pushed intent in the live store
guardianctl -format json intent validate --partition <partition> --intent <intent>

# Inspect what would change
guardianctl -format json intent plan --partition <partition>
```

`partition validate-local` validates every subdirectory under `--root`; focus on
errors for your partition and do not "fix" unrelated partitions unless asked.

## Release

Use the `guardianctl-release` skill for deployment. At minimum:

```bash
guardianctl release run --partition <partition> --dry-run
guardianctl release run --partition <partition>            # config-only
guardianctl release run --partition <partition> --build --bump
```

Or push a local bundle:

```bash
guardianctl partition push --dir stratatools/partitions/<partition>
guardianctl partition reconcile --partition <partition>
guardianctl partition wait --partition <partition> --status Healthy
```

Proposal-first:

1. show the files or diff
2. run validation and plan
3. ask for approval
4. push, reconcile, and wait
5. verify with `query-agent-telemetry` and `partition status`

## Completion checklist

- [ ] existing bundle reviewed
- [ ] asset catalog and `asset describe` consulted
- [ ] partition manifest valid and correctly named
- [ ] intents declare joins, dependsOn, and target pusher/cluster/namespace
- [ ] assets use catalog-backed types and valid properties
- [ ] secrets remain late-bound
- [ ] validation and plan run
- [ ] user approved before mutation
- [ ] post-deploy health verified
