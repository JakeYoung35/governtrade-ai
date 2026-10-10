# Setup and operations

Technical setup for running GovernTrade AI locally. For a product overview, see the [README](../README.md).

## Requirements

- Python 3.12 or later
- Node.js (optional, only for `node --check` syntax checks)

## Run locally

macOS and Linux:

    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -r requirements.txt
    python server.py

Open http://localhost:3110 and enter the local workspace. Set `PORT=3111` (or another free port) if 3110 is in use. The entry screen is a local welcome screen, not hosted authentication. Keep the server on localhost and do not expose it through a public tunnel.

## Environment variables

- `PORT`: optional, default 3110.
- `GOVERNTRADE_MASTER_KEY`: required when macOS Keychain is unavailable (Windows and Linux). A Fernet key used to encrypt saved credentials. See below.
- `GOVERNTRADE_AGENT_TOKEN`: required only for the trusted AI worker HTTP integration. Use a random secret. Without it, worker endpoints are disabled.
- `DATABASE_URL`: optional Postgres connection string. When set, the main workspace is stored in Postgres instead of `.data/main.json`. See Database storage.

## Credential encryption

Kalshi and LLM provider keys are encrypted with Fernet before they are saved, and are only ever shown as last-four masks.

On macOS, the encryption key is created in Keychain automatically on the first credential save.

On other systems, set `GOVERNTRADE_MASTER_KEY`. Store it outside the repository. Do not commit it, paste it in chat, or save it in a plaintext `.env` file. Keep using the same key afterward, since saved credentials can only be decrypted with the key that encrypted them.

### Windows setup

In PowerShell, from the project folder:

    python -m venv .venv
    .venv\Scripts\Activate.ps1
    pip install -r requirements.txt

If PowerShell blocks the activate script, run this first. It only applies to the current terminal window:

    Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process

Generate a key and set it for the current terminal in one step, so it is never copied by hand:

    $env:GOVERNTRADE_MASTER_KEY = python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Save the same key as a user environment variable so new terminals pick it up:

    [Environment]::SetEnvironmentVariable("GOVERNTRADE_MASTER_KEY", $env:GOVERNTRADE_MASTER_KEY, "User")

Check that it is 44 characters long:

    $env:GOVERNTRADE_MASTER_KEY.Length

Then start the server with `python server.py`.

Windows notes:

- Use `python` instead of `python3`.
- Check that your terminal prompt starts with `(.venv)`. If pip says "Defaulting to user installation", the virtual environment is not active.
- If saving a credential fails with "Fernet key must be 32 url-safe base64-encoded bytes", the key is incomplete, usually from a copy and paste that dropped the final `=`. Generate a new one with the commands above.
- If you set `GOVERNTRADE_MASTER_KEY` as a user environment variable while Cursor, VS Code or another editor was open, its terminals will not see it until the editor is fully restarted. To load it into an open terminal without restarting, run `$env:GOVERNTRADE_MASTER_KEY = [Environment]::GetEnvironmentVariable("GOVERNTRADE_MASTER_KEY","User")`.

## Connect Kalshi

Connected Accounts offers **Kalshi Demo** (virtual money at `external-api.demo.kalshi.co`) and **Kalshi Live** (real money at `external-api.kalshi.com`). Live requires an explicit real-money confirmation. Demo and Live need their own keys, created on the matching Kalshi site.

- Create an **RSA** API key on Kalshi. The app does not yet support Ed25519 keys, which Kalshi recommends by default.
- Enter the API key ID and the full RSA PEM private key, including the `-----BEGIN` and `-----END` lines.
- Choose subaccount 0 (primary) or a numbered subaccount from 1 to 63. Portfolio reads and orders are scoped to that subaccount.
- Click **Test connection** to sync the balance.

The environment and subaccount of a saved connection cannot be changed. Create a separate connection instead. To rotate keys, edit the connection. Leaving the credential fields blank keeps the saved keys. Disconnecting pauses bound agents and removes credentials after outstanding submissions are reconciled.

## Database storage

Without `DATABASE_URL`, the app uses local JSON files in `.data/`. With it, the main workspace is stored in Postgres (developed against Tiger Cloud/TimescaleDB). The presenter demo always stays in local JSON. Credentials never leave the local encrypted vault. The database holds only opaque vault references and masks, so the same vault key is still required on any machine using the database.

`migrations/` holds the schema. The server applies pending migrations at startup, or run `DATABASE_URL=... python store.py migrate` to apply them without starting the server.

With Tiger Cloud, run the server without printing the password:

    DATABASE_URL="$(tiger db uri <service-id> --with-password)" python server.py

Never commit the connection string or put it in a plaintext `.env` file. Existing `.data/main.json` contents are not imported automatically.

## Local data

`.data/` contains the private local store, encrypted vault and separate demo state, and is ignored by Git. Local API protections include host and origin validation, HttpOnly SameSite cookies and CSRF tokens. They do not isolate the app from other trusted processes running as the same OS user.

## Verification

    python -m unittest discover -s . -p "test_*.py" -v
    node --check static/app.js

On macOS and Linux, `python3` also works. Database tests are skipped unless Postgres is configured.

Kalshi Demo connection, balance sync, LLM key validation, mandate setup, and credential persistence across restarts have been tested with real credentials. Authenticated order placement has not yet been exercised. Run demo acceptance tests before enabling real money.
