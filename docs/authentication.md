# Authentication and namespace access

The unified FastAPI backend uses persistent application identities. Its Admin
and Operator roles have no relationship to Kubernetes roles or credentials.
All Kubernetes operations remain read-only.

## Local setup and first Admin

Run from the repository root:

```bash
uv sync --locked
# Copy only when you do not already have a local configuration:
cp .env.example .env
chmod 600 .env
uv run --locked python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Store the generated value as `AIOPS_JWT_SECRET` in the private `.env` file or
inject it through your environment/secret manager. Set `GROQ_API_KEY` too.
There is no fallback signing secret and no default account. Never commit either
secret. Exported environment variables take precedence over `.env`.

| Variable | Default / requirement |
| --- | --- |
| `AIOPS_JWT_SECRET` | Required; at least 32 bytes, generated randomly. |
| `AIOPS_JWT_ISSUER` | `aiops-backend` |
| `AIOPS_JWT_AUDIENCE` | `aiops-api` |
| `AIOPS_ACCESS_TOKEN_MINUTES` | `15`; allowed range 1–60. |
| `AIOPS_DATABASE_URL` | `sqlite:///./aiops.db`; relative to the working directory. |

Create the schema explicitly, then bootstrap the first Admin:

```bash
uv run --locked alembic upgrade head
uv run --locked python -m backend.auth.bootstrap --username admin --namespace aiops
uv run --locked uvicorn backend.main:app --host 127.0.0.1 --port 8005 --no-access-log
```

The bootstrap prompts twice using `getpass`; passwords never enter command-line
arguments. It refuses terminals without hidden input, accepts nonempty Admin
passwords up to 1024 characters, and works only while the users table is
empty. Subsequent accounts must be created by an authenticated Admin. Repeat
`--namespace` for each permitted namespace. Omitting it grants no cluster access.
The CLI and migrations create local database files with a private umask.

Admin passwords may contain 1–1024 characters; Operator passwords require
12–1024 characters.

Startup checks database readiness and fails if migrations or signing
configuration are missing. It never runs `create_all` or applies migrations.
Back up the database before applying future migrations. `alembic downgrade base`
is destructive and intended only for disposable development databases.

## API examples

Login uses an OAuth2 password form. This example reads the password without
echoing it and sends the encoded body through stdin, avoiding password arguments
in process listings. `TOKEN` is a short-lived bearer credential; keep it private.

```bash
TOKEN=$(
  uv run --locked python -c '
from getpass import getpass
from urllib.parse import urlencode
print(urlencode({"username": "admin", "password": getpass("Admin password: ")}))
' | curl -fsS http://127.0.0.1:8005/auth/token \
    -H 'Content-Type: application/x-www-form-urlencoded' --data-binary @- \
    | uv run --locked python -c 'import json, sys; print(json.load(sys.stdin)["access_token"])'
)

curl -fsS http://127.0.0.1:8005/auth/me -H "Authorization: Bearer $TOKEN"
```

Create an Operator with access only to `aiops`:

```bash
uv run --locked python -c '
import json
from getpass import getpass
print(json.dumps({"username": "operator", "password": getpass("New Operator password: "),
                  "role": "operator", "allowed_namespaces": ["aiops"]}))
' | curl -fsS http://127.0.0.1:8005/users \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' --data-binary @-

curl -fsS http://127.0.0.1:8005/agent/investigate \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"namespace":"aiops","message":"Investigate Pod crash-demo in aiops."}'

curl -fsS 'http://127.0.0.1:8005/api/v1/pods/aiops/crash-demo/evidence?tail_lines=20' \
  -H "Authorization: Bearer $TOKEN"
```

To investigate as the Operator, repeat login with `username` set to `operator`
and use that token. An Admin can list users with `GET /users?offset=0&limit=100`
and modify permissions with:

```bash
curl -fsS -X PATCH "http://127.0.0.1:8005/users/$USER_ID" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"allowed_namespaces":["aiops"],"is_active":false}'
unset TOKEN
```

`USER_ID` is the immutable UUID returned by creation or listing. PATCH accepts
only `role`, `allowed_namespaces`, and `is_active`. Roles are exactly `admin` and
`operator`. Usernames are unique, case-sensitive lowercase strings, 3–64
characters, beginning with a letter and containing letters, digits, `_`, `.`, or
`-`. Namespace names must be Kubernetes DNS labels, 1–63 characters; wildcards,
empty names, uppercase letters and dots are rejected. Empty permission lists are
valid and grant no Kubernetes access. At most 100 namespaces can be assigned.

## Identity, tokens, and account rules

The `users` table stores UUID ID, unique username, salted Argon2id password hash,
role, JSON namespace list, active flag, and UTC creation/update timestamps.
JSON and SQLAlchemy UUID/Boolean types keep the model portable to PostgreSQL.
Migration `0001_users` creates it and a singleton `user_write_lock` row.
PostgreSQL requires installing its database driver and configuring the URL;
PostgreSQL deployment/migration testing is outside this change.

`POST /auth/token` verifies the password, returns a bearer token with a 15-minute
default lifetime, and upgrades an old Argon2 hash after successful login when
needed. A dummy Argon2 verification covers unknown usernames. Unknown users,
wrong passwords and disabled accounts receive the same generic `401` response.
Passwords are never logged. There is no public signup or refresh-token endpoint.

JWTs use only HS256. Verification explicitly permits that algorithm and requires
`sub`, `exp`, `iat`, `nbf`, `iss`, and `aud`, validating signature, expiration,
issuer, audience, and activation times. The subject is the user UUID. Tokens do
not carry authoritative roles or namespace permissions. Each protected request
loads the current account from the database, so disabling an account, changing
its role or removing a namespace takes effect on its next request, even with an
existing unexpired token. Deleting an account also invalidates its token.

| Access | Admin | Operator | Anonymous |
| --- | --- | --- | --- |
| `/health` | Yes | Yes | Yes |
| `/auth/me` | Own identity | Own identity | 401 |
| `POST /users`, `GET /users`, `PATCH /users/{user_id}` | Yes | 403 | 401 |
| Agent and evidence endpoints | Explicitly allowed namespaces | Explicitly allowed namespaces | 401 |

Operators cannot self-promote or edit any account. Admins can create other
Admins and edit their own permitted fields, subject to the last-admin guard.
All account writes obtain a database lock before rechecking the actor and
checking whether a change removes the final active Admin. This serializes
concurrent disabling/demotion and competing bootstraps across processes.
Duplicate usernames return `409`; removing the last active Admin returns `409`.
Unknown or disabled tokens return `401` with `WWW-Authenticate: Bearer`.

Public user schemas allowlist fields; hashes are never serialized. Validation
errors omit input values and exception context, so malformed password requests
cannot echo credentials. SQLAlchemy disables SQL parameter rendering. Known
environment secrets, JWTs and Argon2 hashes are redacted from investigation logs.

Login limits are five attempts per username and twenty per peer IP in a rolling
five-minute window. Both successful and failed attempts count; `429` includes
`Retry-After`. The limiter is thread-safe and has bounded memory. Forwarded
headers are ignored by the application limiter; configure Uvicorn's trusted
proxy addresses carefully if using proxy headers. Username keys are hashed
in memory. Restarting a worker resets its limiter.

## Authorization at the tool boundary

The shared two-node reactive graph and message-only `AgentState` remain intact.
FastAPI creates a frozen `AuthorizationContext` with a UUID and a frozenset of
allowed namespaces. `StateGraph(..., context_schema=AuthorizationContext)` and
`astream(..., context=context)` carry it through LangGraph's installed runtime
API. Every one of the five LangChain tools calls `require_tool_namespace` before
entering any collector. `ToolRuntime` is injected by `ToolNode` and excluded from
the LLM tool schema; generated arguments cannot supply trusted permissions.

Missing context or a forbidden namespace raises `NamespaceAccessDenied` before
Kubernetes I/O. It propagates out of the tool loop, and the investigation endpoint
returns `403` with a safe failure response. Direct evidence uses the same current
database permissions before entering its collector. There is no global mutable
current user, session, or permission variable.

The optional structured `namespace` field is checked before graph execution and
narrows that investigation to exactly that namespace. Omitting it preserves the
existing message-only request interface; generated tool calls remain limited to
the user's explicit namespace allowlist. Free text is never parsed as an
authorization decision. Independent trusted Python callers must now pass an
`AuthorizationContext` when invoking tools/graphs; missing context fails closed.
Standalone diagnostic collectors/CLI remain local administrative interfaces
using the process's Kubernetes credentials, outside the HTTP application roles.

## Verification and remaining limitations

```bash
uv run --locked python -m unittest tests.test_auth tests.test_agent_workflow tests.test_backend -v
RUN_K8S_INTEGRATION=0 uv run --locked python -m unittest discover -s diagnostic-service/tests -v
uv run --locked python -m unittest discover -s helm/sentinelshop/tests -v
```

Security tests migrate temporary SQLite databases and exercise real Argon2id and
JWT validation. Kubernetes collectors and the model are mocked; no Groq calls
or live cluster access are used. They cover login failures, claim validation,
role restrictions, self-escalation, disabled accounts, last-admin concurrency,
per-tool denial, forged tool arguments, concurrent HTTP requests with distinct
permissions, response/log credential exclusion, and migration/bootstrap behavior.

**Deployment blocker: dedicated least-privilege Kubernetes credentials are not
implemented.** The collectors still load the process's current kubeconfig. Before
production deployment, provide a dedicated read-only ServiceAccount/credential
and namespace-scoped Roles/RoleBindings: `get`/`list` on Pods, `get` on `pods/log`,
`list` on events, and optional `get` on `metrics.k8s.io` Pods. Do not bind the app
Admin role to Kubernetes cluster-admin. This change grants no Kubernetes writes
and does not claim complete production security.

Other deployment work remains:

- Use HTTPS and a secret manager, restrict database/backups and log access, and
  rotate the signing key operationally. Rotating it invalidates existing tokens;
  individual token revocation/logout, MFA and password reset are not implemented.
- Enforce shared login limits at a trusted ingress before using multiple workers
  or replicas; the built-in limiter is per process and resets on restart. Add
  investigation admission/resource controls for expensive model/cluster calls.
- Permissions are a per-request snapshot. Already running investigations finish
  under their initial context; account disablement blocks subsequent requests.
- Protect diagnostic evidence sent to the configured external model provider.
  Kubernetes logs may contain arbitrary application data; existing bounded
  collection and redaction do not guarantee removal of all workload secrets.
- `/docs` and `/openapi.json` remain public API descriptions; `/health` returns
  only process status and performs no cluster or model call. Health is served
  after successful configured startup.

Library references: [PyJWT validation](https://pyjwt.readthedocs.io/en/latest/usage.html)
and [argon2-cffi PasswordHasher](https://argon2-cffi.readthedocs.io/en/stable/api.html).

## Change inventory and verified results

| Created files | Purpose |
| --- | --- |
| `backend/auth/__init__.py`, `config.py`, `database.py`, `models.py` | Auth package, environment configuration, session lifecycle and portable models. |
| `backend/auth/schemas.py`, `security.py`, `dependencies.py` | Validated input/public output, hashing/JWTs, current-account and role/namespace checks. |
| `backend/auth/accounts.py`, `routes.py`, `rate_limit.py`, `bootstrap.py` | Serialized account changes, HTTP endpoints, login limiter and hidden-prompt bootstrap. |
| `agent/authorization.py` | Immutable trusted run context and tool-boundary enforcement. |
| `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/0001_users.py` | Explicit portable schema migration workflow. |
| `tests/auth_support.py`, `tests/test_auth.py` | Temporary migrated databases and offline security regression tests. |
| `docs/authentication.md` | Setup, API examples, design and security limits. |

| Modified files | Change |
| --- | --- |
| `backend/main.py`, `backend/logging_setup.py` | Auth startup, protected investigations, runtime context, safe validation/database errors and hash redaction. |
| `agent/graph.py`, `agent/tools/_common.py`, `agent/tools/{events,logs,metrics,pod_info,pod_listing}.py` | Declare trusted context and check namespaces before all five collectors. |
| `diagnostic-service/api.py` | Authenticate and authorize direct evidence requests; retain public health. |
| `tests/test_agent_workflow.py`, `tests/test_backend.py`, `diagnostic-service/tests/test_api.py` | Preserve regression assertions while supplying authenticated identities/context and updated API paths. |
| `pyproject.toml`, `uv.lock` | SQLAlchemy, Alembic, Argon2, PyJWT and form parsing dependencies. |
| `.env.example`, `.gitignore`, `.github/workflows/ci.yml` | Auth configuration examples, ignored local databases, security suite in CI. |
| `README.md`, `diagnostic-service/README.md` | Authenticated setup/requests and current deployment limitations. |

Verified locally: 66 auth/agent/backend tests passed; 37 diagnostic tests passed
and one opt-in live Kubernetes test was skipped; all three Helm tests passed.
`uv sync --locked --offline`, `uv pip check`, Python compilation, Alembic
upgrade/downgrade/metadata consistency, Helm lint/render, and `git diff --check`
passed. FastAPI TestClient suites ran outside the filesystem/network sandbox
because its event-loop thread stalled inside the sandbox. Model and Kubernetes
calls were still mocked. No credentials were added to the repository, and no
commit or push was performed.
