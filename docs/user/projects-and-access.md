# Projects and access

## Roles

A user has one role per project, plus an optional global `admin` flag. Roles are cumulative.

| Role | Can additionally |
|---|---|
| `viewer` | list the project, read findings, workflows, trace links; run queries and exports |
| `triager` | change the triage status of findings |
| `maintainer` | upload artifacts (`sandman ingest`, console upload), trigger workflows |
| global `admin` | manage users, create projects, assign project roles; acts as `maintainer` in every project |

A user without a role in a project does not see it: its URLs answer `404 not found`, not `403`. A role that is too low answers `403` with `role <x> required`.

## Users (admin only)

Console: **Administration** (`/console/admin`, visible to admins only). It offers:

- **Create user**: user name (`[A-Za-z0-9._@-]`, 2 to 64 characters), initial password (at least 12 characters), optional "global admin". The user must change the password at the first login.
- **Disable / Enable**: a disabled user cannot log in and their tokens stop working; disabling ends their sessions.
- **Reset password**: sets a new password (at least 12 characters). A reset for another user forces a password change at their next login. There is no user deletion.
- **Set project role**: user name, project id, role (`viewer`, `triager`, `maintainer`). Setting a role again changes it. **Remove** next to a membership withdraws it. The console only checks the project id's format, not that the project exists yet.

The same operations exist in the REST API (admin token): `GET/POST /v1/admin/users`, `PATCH /v1/admin/users/{username}`, `PUT/DELETE /v1/admin/projects/{projectId}/members/{username}` with body `{"role":"viewer"}`. Example:

```sh
curl -X POST "$SANDMAN_URL/v1/admin/users" -H "Authorization: Bearer $SANDMAN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"username":"alice","password":"a-long-initial-password"}'
curl -X PUT "$SANDMAN_URL/v1/admin/projects/demo/members/alice" -H "Authorization: Bearer $SANDMAN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"role":"maintainer"}'
```

## Projects

Projects are created through the API only (admin), see [getting-started.md](getting-started.md):
`PUT /v1/admin/projects/{projectId}` with `{"name": "...", "llmPolicy": "cloud-allowed" | "local-only"}`. Project ids match `^[a-z0-9][a-z0-9-]{1,62}$`. The console page **Projects** lists the projects you can read, with your role, finding counts per status, the number of findings that are new or reopened since your last visit to the project's findings page, and the status of the latest workflow. Links lead to findings, **Ask**, **Workflows** and (for maintainers and admins) **Upload**.

## Personal API tokens

The REST API and the CLI accept only bearer tokens. Session cookies work only in the console.

Console: **Tokens** (`/console/tokens`). Fields:

| Field | Meaning |
|---|---|
| Name | label, 1 to 100 characters |
| Project (optional) | restricts the token to one project |
| Max role (optional) | caps the token's role: `viewer`, `triager` or `maintainer`; the effective role is the lower of your role and the cap |
| Expires (optional) | ISO timestamp such as `2027-01-01T00:00:00Z` |

The secret (`sdm_...`) is shown once; only its hash is stored. The table lists name, project, cap, creation, expiry and last use, with **Revoke**. A token that is restricted (project or cap) cannot create or revoke tokens and cannot act as admin. Use a restricted token for CI jobs: for example project `demo`, cap `maintainer`.

```sh
curl -H "Authorization: Bearer $SANDMAN_TOKEN" http://127.0.0.1:8080/v1/auth/me
```

Tokens also work via `POST /v1/auth/tokens` (body `name`, `projectId`, `maxRole`, `expiresAt`), `GET /v1/auth/tokens` and `DELETE /v1/auth/tokens/{tokenId}`.

Failures to expect: `401` for an unknown, revoked or expired token or a disabled user; `403 password change required` when the token's owner has not yet changed their initial password.
