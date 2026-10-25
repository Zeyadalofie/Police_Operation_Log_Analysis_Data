# Mirsad — Operations Data Analysis Dashboard

Mirsad is a locally run data analysis dashboard for operations data. It combines a React and Vite frontend with a Python and FastAPI backend, and keeps each user's account, data, and integrations separate.

## Features

- Upload and preview CSV and JSON datasets (up to 15 MB).
- Review dataset dimensions, missing values, and summary statistics.
- Create bar and pie charts from selected dataset columns.
- Generate synthetic operations datasets with a per-user local Ollama model; generated records are validated with the rules in `src/TODOs/rules.py`.
- Configure an AI provider per user, including local Ollama and OpenAI-compatible APIs. The chart assistant sends aggregated statistics to the model, not the raw dataset rows.
- Connect Google Drive or S3-compatible storage, choose a per-user destination folder or prefix, and save reports there.
- Sign in with email and password, with optional Google and Apple OAuth configuration.

## Requirements

- Python 3.10 or later
- [uv](https://docs.astral.sh/uv/)
- Node.js and npm

## Run locally on Windows

Open two PowerShell terminals in the project root.

### 1. Install Python dependencies

```powershell
uv sync
```

### 2. Start the backend

In the first terminal, run:

```powershell
uv run python -m uvicorn src.api:app --reload --host 127.0.0.1 --port 8000
```

### 3. Start the frontend

In the second terminal, run:

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

Run `npm.cmd install` once, or again after frontend dependencies change. Open the local URL printed by Vite, usually `http://localhost:5173`.

Use `npm.cmd` in PowerShell if its execution policy blocks `npm.ps1`.

## Configuration

Keep credentials in the local `.env` file; never put secrets in source code or commit `.env` to Git. Set strong values for `SESSION_SECRET` and `CREDENTIALS_ENCRYPTION_KEY`. Add OAuth credentials only when enabling the corresponding sign-in or storage integration.

For a Google OAuth web client used locally, register these authorized redirect URIs:

```text
http://localhost:8000/api/auth/google/callback
http://localhost:8000/api/connections/google-drive/callback
```

If needed, register this authorized JavaScript origin separately:

```text
http://localhost:5173
```

The Google Drive integration requires OAuth configuration on the backend. Each user then connects their own Google account from within the application.

## Production deployment

This repository is configured for local development. Before exposing it publicly, use HTTPS, a managed database, CSRF protection, login rate limits, and production OAuth settings. Review the deployment configuration and secret handling before launch.
