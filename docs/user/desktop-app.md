# Desktop app

The desktop app (`apps/desktop`) works on one SANDMAN project at a time. It starts or connects to the system. It stores your settings, ingests folders and files, and shows progress and results. The app is optional. The console, the REST API and the CLI work as before.

Build and start the app as described in [apps/desktop/README.md](../../apps/desktop/README.md).

## Start screen

The start screen shows the list of your projects. Each row shows the name, the id, the mode (`local` or `remote`) and the URL.

| Button | Action |
|---|---|
| New project | opens the form for a new project |
| Continue <name> | opens the project you used last |
| Open | opens the selected project (a double click does the same) |
| Remove | removes the selected project from the list |

Warning: Remove deletes only the entry in the app. It does not delete the project in SANDMAN. It does not delete files of a checkout.

## Create a local project

A local project uses a SANDMAN checkout on this computer. The app starts the Docker stack of this checkout.

1. Install Docker with Compose. Start Docker.
2. Click New project.
3. Enter a name. The app suggests an id from the name.
4. Change the id if you want. The id matches `^[a-z0-9][a-z0-9-]{1,62}$`.
5. Select the mode `local`.
6. Click Browse. Select the folder of the SANDMAN checkout. The folder must contain `deploy/docker-compose.yml`.
7. Enter the keys. See the next section.
8. Click Create.

The app writes `deploy/secrets/gateway.env` and `deploy/.env` in the checkout. It keeps other lines and comments of these files. The URL of a local project is `http://127.0.0.1:8080`.

## Keys and defaults

| Field | Written to | Meaning |
|---|---|---|
| OpenAI API key | `deploy/secrets/gateway.env` | key for the cloud provider OpenAI |
| OpenRouter API key | `deploy/secrets/gateway.env` | key for the cloud provider OpenRouter |
| Self-hosted model URL | `deploy/secrets/gateway.env` | URL of the self-hosted model server (see [getting-started.md](getting-started.md#2-provider-keys-optional-today)) |
| Self-hosted model key | `deploy/secrets/gateway.env` | key for the self-hosted model server |
| CoreNLP mode | `deploy/.env` | `remote` (default) or `local` (see [getting-started.md](getting-started.md#3-corenlp-service-ardoco-approach)) |

Leave a key empty to keep the current value. A key that is set shows `(set, leave empty to keep)`.

To reuse the keys in other projects, tick "Save as default for new projects". The app stores the default keys in the keychain of the operating system. New projects start with these values.

You can change the keys later in the tab Settings. Restart the stack to apply the change.

## Create a remote project

A remote project uses a SANDMAN system that runs on another computer. The app does not start or stop it.

1. Click New project.
2. Enter a name and an id.
3. Select the mode `remote`.
4. Enter the URL of the system.
5. Paste your personal API token. Leave the field empty to enter it later.
6. Click Create.

Caution: Use an `https://` URL for each host that is not this computer. A token sent over `http://` is readable on the network.

The app stores the token in the keychain of the operating system.

## Start and stop a local stack

The project header of a local project shows the state of the stack. The state is `stopped`, `starting`, `running` or `partial`. The text `(ready)` means that the system answers `/readyz`.

1. Click Start in the project header. The app runs `make up` in the checkout.
2. Wait until the header shows `running (ready)`. The first start takes several minutes.
3. Click Stop to stop the containers. The app runs `make down`. Data is kept.

The output of the commands appears in a log below the header. A remote project has no Start and no Stop button.

If a command fails, the log stays open. The app shows a hint and the last 15 lines. The app detects these causes:

- Docker is not running.
- Port 8080 is in use by another program.
- A `check-env` message about keys or credentials.
- No disk space.

At the start of a local project, the app checks for `make`, `docker` and `uv`. It shows a notice for each program that is missing.

## Close the app or switch the project

The close dialog appears when you close the window or click Switch project. It appears only if the local stack still runs.

| Button | Effect |
|---|---|
| Stop containers | runs `make down`, then closes or switches |
| Keep running (copy command) | copies `make -C <checkout> down` to the clipboard, then closes or switches; the containers keep running |
| Cancel | returns to the project |

If you keep the stack running, stop it later by hand. See [operations.md](operations.md#start-stop-status).

## Create a token

The app needs a personal API token for each project. A project without a token shows numbered steps in the tab Overview. The app cannot create a token itself.

1. For a local project, click Start and wait until the stack runs. Step 1 shows the state.
2. Click Copy admin password. The app copies the initial password of the user `admin` to the clipboard. The app never shows the password.
3. Click Open console login. The system browser opens the console. Sign in as `admin` and change the password if the console asks for it.
4. Click Open console tokens page. Enter a name and click Create.
5. Copy the secret. It starts with `sdm_`. The console shows it once.
6. Return to the app. Paste the secret in the field. Click Save and check.

The app checks the token with `GET /v1/auth/me` before it stores the token. If the check fails, the app shows the reason and keeps the old token.

| Message | Meaning |
|---|---|
| The token is not valid | The system answers 401. Create a new token. |
| The password must be changed | The system answers 403. Open the console login and change the password. |

The initial password is in `deploy/secrets/admin_bootstrap_password` in the checkout. For a remote project, skip steps 1 to 3.

More about users and tokens: [projects-and-access.md](projects-and-access.md).

## Ingest folders and files

Ingest needs a token and the `sandman` CLI. The app starts the CLI for you.

1. Open the tab Ingest.
2. Click Pick folder to select one folder. Or click Pick files to select single files.
3. Select the mode `full` or `partial`. See [ingesting-artifacts.md](ingesting-artifacts.md).
4. Enter a source revision if you want one. The field is optional.
5. Check the command preview and the number of picked items.
6. Click Ingest. Click Cancel to stop the CLI.

The log shows the output of the CLI and the exit code. If the CLI fails, the app shows the last lines of the log.

When the CLI ends, the app looks for the new workflow. It shows the steps and updates them every 2 seconds until the workflow ends.

Single files are always sent as mode `partial`. The app copies them first to a temporary folder. The relative paths below their common parent folder stay the same.

The app runs `uv run --project <checkout> sandman ingest`. A remote project has no checkout. To ingest from a remote project, enter a CLI command in the tab Settings.

## Tabs

| Tab | Content |
|---|---|
| Overview | state of the system, number of findings per status, latest workflow; token steps if no token is set |
| Ingest | ingest folders and files, log, workflow progress |
| Workflows | list of workflows with details and Load more; run a workflow of type `full`, `extract`, `tlr` or `detect` |
| Findings | list with filters for status and severity (default `OPEN,REOPENED`) and Load more; details with evidence and history; triage buttons |
| Trace links | list with filters for status and approach and Load more; buttons `confirmed` and `rejected` |
| Ask | enter a question in natural language; shows answer, rows and Cypher |
| Graph | graph of elements and relations; click a node to load its neighbours |
| Settings | token, CLI command, keys (local project), buttons Open console and Open dashboard |

The tabs Overview and Workflows refresh every 3 seconds while a workflow is `PENDING` or `RUNNING`. They stop when the window is hidden.

Press Ctrl+1 to Ctrl+8 (Cmd+1 to Cmd+8 on macOS) to select a tab. The window title shows the project name and the state of the stack.

Triage of a finding needs a role of `triager` or higher. A triage move to `ACCEPTED`, `REJECTED` or `DEFERRED` needs a reason and a cause. Enter both before you click the button. If another user changed the finding, click Reload finding. See [trace-links-and-findings.md](trace-links-and-findings.md).

## Limitations

- The app has no administration, no password change, no token management and no project creation. Use the console and the REST API.
- Rejecting a trace link needs a reason. Links of the approaches `explicit-reference` and `user-declared` cannot be reviewed.
- The app does not update itself. The bundles are not signed.
- Ingest needs the `sandman` CLI. A remote project without a checkout needs a CLI command in Settings.
- Linux and Windows builds were not tested.
- The app was not yet tested against a running stack. The manual acceptance run is open.
- The graph shows at most 100 relations for each click.

## Troubleshooting

| Symptom | Action |
|---|---|
| The app shows `401` or a message about the token | Paste a valid token in the tab Settings. Click Save. |
| The tab Overview shows "No token yet" | Follow the numbered steps (see above). |
| Start says that port 8080 is in use | Stop the other program. Start again. |
| Start says that Docker is not running | Start Docker. Start again. |
| The state stays `stopped` after Start | Read the log below the header. Check that Docker runs. Run `make up` in the checkout to see the full output. |
| Start fails with a `check-env` message | Fix the keys or `deploy/secrets/corenlp_credentials`. See [getting-started.md](getting-started.md). |
| The system is unreachable | For a local project, click Start. For a remote project, check the URL and the network. |
| Ingest fails with `uv` not found | Install [uv](https://docs.astral.sh/uv/). Or enter another CLI command in Settings. |
| macOS refuses to open the app | Open the app with right click and Open. The bundle is not signed. |
| The app cannot store a token on Linux | Start a Secret Service provider, for example GNOME Keyring. |
