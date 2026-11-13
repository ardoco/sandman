# SANDMAN desktop app

The app works on one SANDMAN project at a time. It starts or connects to the system, stores settings, ingests folders and files, and shows progress and results. Decision: [ADR-0019](../../adr/0019-desktop-app.md).

## Prerequisites

| Tool | Version | Install hint |
|---|---|---|
| Node.js | 22 or newer | https://nodejs.org or `brew install node` |
| Rust | stable | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` |
| Tauri system libraries | n/a | see below |
| Docker with Compose | n/a | https://docs.docker.com/get-docker/ (local projects only) |
| uv | recent | `brew install uv` or https://docs.astral.sh/uv/ (ingestion only) |

Tauri system libraries:

* macOS: run `xcode-select --install`.
* Linux (Debian, Ubuntu): run `sudo apt-get install libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev libsoup-3.0-dev libjavascriptcoregtk-4.1-dev build-essential`.
* Windows: install the Microsoft C++ Build Tools and the WebView2 runtime.

The OS keychain stores tokens and key store values (service `sandman-desktop`, account `key:<id>`). Labels and URL values are in `keystore.json`. On Linux, a Secret Service provider (for example GNOME Keyring) must run.

## Install dependencies

```sh
cd apps/desktop
npm ci
```

## Run in development

```sh
make desktop-dev          # from the repository root
npm run tauri dev         # or, from apps/desktop
```

The first Rust build takes several minutes.

## Build

```sh
make desktop-build        # from the repository root
npm run tauri build       # or, from apps/desktop
```

Add `-- --no-bundle` to build only the binary.

| Output | Path (below `apps/desktop/src-tauri/target/release/`) |
|---|---|
| Binary | `sandman-desktop` (`sandman-desktop.exe` on Windows) |
| macOS app | `bundle/macos/SANDMAN.app` |
| macOS disk image | `bundle/dmg/SANDMAN_<version>_<arch>.dmg` |
| Linux packages | `bundle/deb/`, `bundle/appimage/`, `bundle/rpm/` |
| Windows installers | `bundle/msi/`, `bundle/nsis/` |

## Start the built app

* macOS: run `open apps/desktop/src-tauri/target/release/bundle/macos/SANDMAN.app`. Or open the `.dmg` and drag the app to Applications.
* Linux: run `apps/desktop/src-tauri/target/release/sandman-desktop`. Or install the `.deb` or run the AppImage.
* Windows: run `apps\desktop\src-tauri\target\release\sandman-desktop.exe`. Or run the installer.

The bundles are not signed. On macOS, open the app with right click and Open on the first start.

## Test

```sh
make desktop-test         # npm test and cargo test
npm run typecheck         # from apps/desktop
```

## Use

### Local project

1. Click New project. Enter a name and an id.
2. Select mode local. Select the SANDMAN checkout folder. The folder must contain `deploy/docker-compose.yml`.
3. If the line shows `Secrets: missing`, click Generate secrets. The button runs `make secrets` and never overwrites files.
4. Enter the keys. Use From store to fill a field. Keys are saved to the key store unless you clear "Save to key store". Tick "Save as default" to reuse them for new projects.
5. Click Create. The app writes `deploy/secrets/gateway.env` and `deploy/.env`.
6. Click Start in the project header. The app runs `make up` in the checkout. The URL is `http://127.0.0.1:8080`.
7. Create a token (see below) and paste it in Settings, tab Project.

When you close the app or switch the project while the stack runs, a dialog asks what to do. Choose Stop containers, Keep running, or Cancel.

### Remote project

1. Click New project. Select mode remote.
2. Enter the URL of the SANDMAN system. Use `https://` for hosts that are not on this machine.
3. Paste the token. The app stores it in the OS keychain.

The app does not start or stop a remote system.

### Create a token

The Overview tab shows numbered steps when the project has no token.

1. Click Copy admin password. The initial password is in `deploy/secrets/admin_bootstrap_password`. The app never shows it.
2. Click Open console login. Sign in as `admin`. Change the password if the console asks for it.
3. Click Open console tokens page. Create a personal API token.
4. Paste the token and click Save and check. The app calls `GET /v1/auth/me` before it stores the token.

### Ingest

1. Open the Ingest tab. Click Pick folder or Pick files.
2. Select the mode. Enter a revision if you want one.
3. Check the command preview. Click Ingest. Click Cancel to stop the CLI.
4. The log shows the CLI output. The app follows the new workflow until it ends.

The app runs `uv run --project <checkout> sandman ingest`. Set a CLI command in Settings, tab Project, to use another command. Single files are staged in a temporary folder and sent with mode partial.

### Shortcuts

Press Ctrl+1 to Ctrl+9 (Cmd on macOS) to select a tab. The start screen offers Connect for a running system, Run locally, Continue for the last project and Settings. Settings has the tabs General (language, theme, font size, default checkout, saved systems) and Project.

## Limitations

* The app has no administration, no token management and no password change.
* The app does not update itself. The bundles are not signed.
* Ingestion needs the `sandman` CLI. A remote project without a checkout needs a CLI command in Settings, tab Project.
* Linux and Windows builds were not tested.
* The app was not tested against a running stack in this version.
