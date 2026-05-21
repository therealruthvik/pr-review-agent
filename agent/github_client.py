"""GitHub REST API v2022-11-28 client — all network objects lazy-loaded."""
import base64
import os
from typing import Optional

import requests

_token: Optional[str] = None
_repo_name: Optional[str] = None

BASE = "https://api.github.com"


def _token_val() -> str:
    global _token
    if _token is None:
        _token = os.environ["GITHUB_TOKEN"]
    return _token


def _repo_val() -> str:
    global _repo_name
    if _repo_name is None:
        _repo_name = os.environ["REPO"]
    return _repo_name


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_token_val()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_pr(pr_number: int) -> dict:
    r = requests.get(
        f"{BASE}/repos/{_repo_val()}/pulls/{pr_number}", headers=_headers(), timeout=30
    )
    r.raise_for_status()
    return r.json()


def get_pr_files(pr_number: int) -> list[dict]:
    files: list[dict] = []
    page = 1
    while True:
        r = requests.get(
            f"{BASE}/repos/{_repo_val()}/pulls/{pr_number}/files",
            headers=_headers(),
            params={"per_page": 100, "page": page},
            timeout=30,
        )
        r.raise_for_status()
        batch: list[dict] = r.json()
        files.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return files


def get_file_content(path: str, ref: str = "HEAD") -> str:
    r = requests.get(
        f"{BASE}/repos/{_repo_val()}/contents/{path}",
        headers=_headers(),
        params={"ref": ref},
        timeout=30,
    )
    if r.status_code == 404:
        return f"[not found: {path}@{ref}]"
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        return f"[directory, not a file: {path}]"
    if data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    return data.get("content", "")


def search_code(pattern: str) -> list[dict]:
    r = requests.get(
        f"{BASE}/search/code",
        headers=_headers(),
        params={"q": f"{pattern} repo:{_repo_val()}"},
        timeout=30,
    )
    r.raise_for_status()
    items = r.json().get("items", [])
    return [{"path": i["path"], "url": i["html_url"]} for i in items[:10]]


def post_review(
    pr_number: int,
    commit_sha: str,
    body: str,
    event: str,
    comments: list[dict],
) -> dict:
    payload = {
        "commit_id": commit_sha,
        "body": body,
        "event": event,
        "comments": comments,
    }
    r = requests.post(
        f"{BASE}/repos/{_repo_val()}/pulls/{pr_number}/reviews",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()
