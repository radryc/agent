---
name: agent-sso-setup
description: "Use when configuring or troubleshooting SSO/OIDC for the agent, when Doctor or Guardian returns 401, or when the agent needs to query logs and traces under per-user access control."
---

# Configure SSO for the agent

Use this skill when the user wants the agent to sign in with SSO, when a
Doctor log/trace query returns `401 unauthorized`, or when Guardian access
control rejects agent API calls.

## What the agent supports

The backend (`app/auth/oidc.py`) supports three auth sources, in order:

1. **Static token** — `AGENT_OIDC_TOKEN` (break-glass or pre-issued bearer).
2. **Cached device-flow token** — interactive SSO from
   **Settings -> SSO -> Sign in with SSO**.
3. **Client credentials** — `AGENT_OIDC_CLIENT_ID` +
   `AGENT_OIDC_CLIENT_SECRET`, used when no user token is cached.

The resulting bearer token is attached to Doctor and Guardian HTTP calls and
to MonoFS session/search tools. The same token is passed to `guardianctl` as
`GUARDIAN_MONOFS_TOKEN`, so direct MonoFS router access works without Guardian
UI discovery.

## Configuration

Set these on the backend (environment or the Settings page):

```text
AGENT_OIDC_ISSUER
AGENT_OIDC_TOKEN_URL
AGENT_OIDC_DEVICE_AUTHORIZATION_URL
AGENT_OIDC_CLIENT_ID
AGENT_OIDC_CLIENT_SECRET
AGENT_OIDC_AUDIENCE
AGENT_OIDC_SCOPE
AGENT_OIDC_TOKEN
```

Choose one mode:

- **Machine-to-machine** — client ID + secret. The backend obtains and caches
  a token automatically. Best for the deployed agent.
- **Interactive SSO** — device authorization URL + token URL + client ID.
  The user opens the verification URL, enters the code, and the backend caches
  the resulting user token.
- **Break-glass** — static token only. Use for recovery, not routine access.

## In-cluster Authentik example

```text
AGENT_OIDC_ISSUER=http://authentik.authentik.svc.cluster.local:9000/application/o/monofs/
AGENT_OIDC_TOKEN_URL=http://authentik.authentik.svc.cluster.local:9000/application/o/token/
AGENT_OIDC_DEVICE_AUTHORIZATION_URL=http://authentik.authentik.svc.cluster.local:9000/application/o/device/
AGENT_OIDC_CLIENT_ID=monofs
AGENT_OIDC_CLIENT_SECRET=<from the shared secret store>
AGENT_OIDC_AUDIENCE=monofs
AGENT_OIDC_SCOPE=openid
```

The token URL must use the same issuer that Doctor validates. Do **not** use
`http://localhost:9020/...` from inside the cluster: the token will carry a
different `iss` and Doctor will reject it.

Seed the client secret before releasing the agent partition:

```bash
guardianctl partition push --dir stratatools/partitions/shared \
  --include-secrets --remove-missing=false
```

The agent partition references it as
`monofs-secret://shared/agent-oidc-client-secret`.

## Verify

Inside the agent, call the native `agent_sso_status` tool. From a terminal:

```bash
curl -sS http://localhost:8090/api/auth/status | jq .
```

Expected shape:

```json
{
  "configured": true,
  "authenticated": true,
  "source": "client_credentials",
  "subject": "",
  "expires_at": 1792504999.6,
  "error": "",
  "device_flow_available": true
}
```

Then verify a protected Doctor call:

```bash
kubectl -n doctor port-forward svc/<doctor-query-service> 18080:18080
curl -sS --get http://127.0.0.1:18080/v1/logs/services \
  --data-urlencode 'tenant=default' | jq .
```

A `401` means the token was not attached, expired, or was issued by a
different issuer. Check `/api/auth/status` and the backend logs.

## Troubleshooting

- **`401 unauthorized` from Doctor** — issuer/audience mismatch, expired
  token, or missing `Authorization` header. Confirm the token URL is the
  in-cluster URL and `AGENT_OIDC_AUDIENCE=monofs`.
- **`invalid_client` on device poll** — the provider expects the client secret
  in the token request; set `AGENT_OIDC_CLIENT_SECRET` or use the client
  credentials flow.
- **Device flow unavailable** — set `AGENT_OIDC_DEVICE_AUTHORIZATION_URL` and
  `AGENT_OIDC_CLIENT_ID`.
- **Guardian denies the call** — a client-credentials token represents the
  service, not a user. Use device flow to get the user's subject, or grant the
  service principal access in the Guardian access policy.
- **Token expires** — the provider defaults to one hour; the backend refreshes
  client credentials automatically and re-runs device login when needed.

## Guardrails

- Never disable Doctor access enforcement just to make a query work.
- Never log access tokens or client secrets.
- Keep the client secret in the shared MonoFS secret store, not in a manifest.
- Device-flow state is process-global; the current agent is a single-operator
  assistant. Multi-user deployments should key device flows by session.

## Completion checklist

- [ ] mode chosen: client credentials, device flow, or static token
- [ ] issuer/token URL/audience match the provider Doctor trusts
- [ ] client secret stored outside the manifest
- [ ] `/api/auth/status` reports `authenticated: true`
- [ ] a protected Doctor logs/traces call succeeds
- [ ] the token is not present in logs or API responses
