# RegPilot

RegPilot is a Python 3.11+ runner for account registration, OAuth callback handling, token exchange, account archiving, and optional callback submission into CPA.

## Current Status

The current implementation includes the full registration chain used by this project:

1. `/api/accounts/authorize`
2. `/api/accounts/user/register`
3. `/api/accounts/email-otp/send`
4. `/api/accounts/email-otp/validate`
5. `/api/accounts/create_account`
6. `platform.openai.com/auth/callback -> oauth/token`

Implemented capabilities:

- PKCE and authorization session setup
- Runtime environment profile selection: proxy, UA, language, timezone, viewport
- Temporary mailbox creation and OTP polling
- Registration submit, email OTP, about-you submit, account creation
- OAuth callback extraction and token exchange
- CLI and FastAPI management API
- Account persistence in `data/accounts.db`
- Result persistence in `data/last_result.json`
- CPA callback submission and account import helpers
- Existing account reauthorization with email OTP and optional phone verification

## Install

```bash
cd /path/to/RegPilot
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

For development/test tools:

```bash
pip install -e '.[dev]'
```

## CLI Usage

Run one registration task:

```bash
regpilot register --config /path/to/config.json
```

Override proxy directly:

```bash
regpilot register --config /path/to/config.json --proxy 'socks5://user:pass@host:port'
```

## FastAPI Management API

```bash
scripts/api.sh
```

Managed background mode:

```bash
scripts/manage-api.sh start
scripts/manage-api.sh status
```

The API scripts support these environment variables:

- `REGPILOT_HOME`: project directory, defaults to the parent of `scripts/`
- `REGPILOT_HOST`: bind host, defaults to `0.0.0.0`
- `REGPILOT_PORT`: bind port, defaults to `8766`
- `PYTHON_BIN`: Python executable, defaults to `python3`
- `REGPILOT_VENV`: virtual environment directory; defaults to `.venv-linux312`, `.venv-linux`, `.venv_linux`, then `.venv`

## Config Example

CPA settings use the existing `codex2api_*` config keys for compatibility with the running WebUI and stored NAS config.

```json
{
  "proxy": "socks5://user:pass@host:port",
  "mail": {
    "request_timeout": 30,
    "wait_timeout": 60,
    "wait_interval": 2,
    "providers": [
      {
        "type": "cloudflare-temp-email",
        "base_url": "https://apimail.example.com",
        "admin_auth": "REPLACE_ME",
        "domain": "example.com"
      },
      {
        "type": "hotmail-api",
        "base_url": "http://127.0.0.1:17373"
      }
    ]
  }
}
```

Mail providers are tried in order. If a configured provider fails during mailbox creation, RegPilot tries the next provider.

## Output

Successful or failed CLI runs print a JSON summary and save the full result to:

- `data/last_result.json`

Common fields:

- `email`
- `password`
- `access_token`
- `refresh_token`
- `id_token`
- `mailbox`
- `callback_url`
- `error`

## Check

Run local syntax and unit checks:

```bash
scripts/check.sh
```

On Windows without Bash, use:

```powershell
$env:PYTHONPATH="src"
.venv\Scripts\python.exe -m compileall -q src tests
.venv\Scripts\python.exe -m unittest discover -s tests -p "test*.py"
```

## Notes

- Proxy quality is a key variable for registration success.
- `data/` and `logs/` contain runtime data and are intentionally ignored by Git.
- Treat `data/last_result.json`, account database files, API keys, and tokens as sensitive.
