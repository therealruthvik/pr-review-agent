# PR Review Agent

Autonomous GitHub PR reviewer powered by Gemini. Runs as a GitHub Actions workflow on every PR — fetches the diff, runs security scans, iterates an agentic tool-use loop, and posts structured inline comments + a verdict.

## What it does

- Fetches PR diff (top 20 files by size, 300 lines/file)
- Runs static security scan on added lines (bandit + 10 custom patterns)
- Launches a Gemini agentic loop (up to 15 turns) with 5 tools:
  - `get_file_content` — fetch full file for context beyond the diff
  - `search_codebase` — find related symbols across the repo
  - `run_security_check` — re-run security scan on specific snippets
  - `add_inline_comment` — queue line-level review comments
  - `finalize_review` — post complete review + verdict to GitHub
- Posts inline comments + overall review with verdict: `APPROVE / REQUEST_CHANGES / COMMENT`

## Prerequisites

- Python 3.12+
- A GitHub repo with Actions enabled
- A [Google AI Studio](https://aistudio.google.com/) API key (Gemini)

## Setup

### 1. Add GitHub Secrets

In your repo: **Settings → Secrets and variables → Actions → Secrets**

| Secret | Value |
|--------|-------|
| `GEMINI_API_KEY` | Your Google AI Studio API key |

`GITHUB_TOKEN` is provided automatically by Actions — no setup needed.

### 2. (Optional) Set model via repo variable

**Settings → Secrets and variables → Actions → Variables**

| Variable | Default | Notes |
|----------|---------|-------|
| `GEMINI_MODEL` | `gemini-2.5-flash` | Verify name at [ai.google.dev/gemini-api/docs/models](https://ai.google.dev/gemini-api/docs/models) before changing |

### 3. Install workflow

The workflow file is already at `.github/workflows/pr-review.yml`. Push it to your repo — it fires automatically on every PR open, push, or reopen.

### 4. Set workflow permissions

**Settings → Actions → General → Workflow permissions** → select **"Read and write permissions"**

Or scope it per-repo in the workflow (already set in `pr-review.yml`):
```yaml
permissions:
  pull-requests: write
  contents: read
```

## Running locally

```bash
# Install dependencies
pip install -r requirements.txt

# Set required env vars
export GEMINI_API_KEY=your_key_here
export GITHUB_TOKEN=your_pat_here   # needs repo + pull_requests read/write
export REPO=owner/repo-name
export PR_NUMBER=42

# Run preflight (required before every run)
make preflight

# Run the review agent
make review
```

### Makefile targets

| Target | What it does |
|--------|--------------|
| `make install` | `pip install -r requirements.txt` |
| `make preflight` | Run all pre-run checks (exits 1 on failure) |
| `make lint` | Syntax-check all agent Python files |
| `make review` | Run the full review agent |

## Preflight checks

`agent/preflight.py` runs 8 checks before every deploy/run:

1. Required env vars set and non-empty
2. Model name not in deprecated list
3. Key imports resolve (`google.genai`, `requests`, `bandit`)
4. `bandit` CLI available
5. Syntax valid on all `agent/*.py` files
6. No module-level client instantiation (lazy init enforced)
7. `.gitignore` exists
8. No deprecated model strings in source or CI files

**Never skip preflight. Never deploy if any check fails.**

## Project structure

```
pr-review-agent/
├── .github/
│   └── workflows/
│       └── pr-review.yml       # Actions trigger: PR opened/sync/reopened
├── agent/
│   ├── __init__.py
│   ├── review_agent.py         # Entry point + agentic loop
│   ├── github_client.py        # GitHub REST API v2022-11-28 (lazy-loaded)
│   ├── tools.py                # Gemini tool declarations + dispatch
│   ├── security_scanner.py     # bandit + custom regex patterns
│   └── preflight.py            # Pre-run checks
├── .gitignore
├── Makefile
└── requirements.txt
```

## Security patterns detected

Custom checks (all files) + bandit (Python only):

| Pattern | Severity |
|---------|----------|
| AWS access key (`AKIA…`) | CRITICAL |
| Hardcoded password/API key/token | HIGH |
| `shell=True` subprocess / `os.system` | HIGH |
| SQL injection via f-string | HIGH |
| `pickle.loads` | HIGH |
| `eval` / `exec` | HIGH |
| Path traversal in `open()` | MEDIUM |
| XML external entity | MEDIUM |
| `assert` in non-test code | LOW |

## Tuning

| What | Where |
|------|-------|
| Max files reviewed | `MAX_FILES` in `review_agent.py` |
| Max diff lines per file | `MAX_DIFF_LINES_PER_FILE` in `review_agent.py` |
| Max agent turns | `MAX_TURNS` in `review_agent.py` |
| Custom security patterns | `_PATTERNS` list in `security_scanner.py` |
| Review prompt / verdict rules | `_SYSTEM_PROMPT` in `review_agent.py` |

## Troubleshooting

**Review not posting** — check Actions logs for preflight failures. Most common: `GEMINI_API_KEY` secret missing or workflow permissions not set to write.

**`400 Unprocessable Entity` from GitHub** — inline comment line number not in the diff. The agent may comment on a line outside the changed hunks; GitHub rejects these. Reduce `MAX_DIFF_LINES_PER_FILE` or tighten the prompt.

**Model name error** — `GEMINI_MODEL` env var set to a retired model. Verify at [ai.google.dev/gemini-api/docs/models](https://ai.google.dev/gemini-api/docs/models) and update the repo variable.
