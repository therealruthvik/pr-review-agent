"""Main agentic review loop: fetch PR → security scan → Gemini loop → post review."""
import os
import sys
from typing import Optional

from google import genai
from google.genai import types

from . import github_client as gh
from .security_scanner import scan_file
from .tools import TOOL, dispatch, get_state, init_state

MAX_FILES = 20
MAX_DIFF_LINES_PER_FILE = 300
MAX_TURNS = 15

_client: Optional[genai.Client] = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _client


def _build_diff(pr_files: list[dict]) -> str:
    # Prioritize files with most changes; skip binary/deleted
    sortable = [f for f in pr_files if f.get("patch") and f.get("status") != "removed"]
    sortable.sort(key=lambda f: f.get("changes", 0), reverse=True)

    parts = []
    for f in sortable[:MAX_FILES]:
        patch = f["patch"]
        lines = patch.splitlines()
        truncated = len(lines) > MAX_DIFF_LINES_PER_FILE
        if truncated:
            patch = "\n".join(lines[:MAX_DIFF_LINES_PER_FILE])
            patch += f"\n[... {len(lines) - MAX_DIFF_LINES_PER_FILE} more lines omitted]"
        parts.append(f"### {f['filename']} ({f['status']})\n```diff\n{patch}\n```")

    return "\n\n".join(parts)


def _gather_security(pr_files: list[dict]) -> str:
    all_findings: list[dict] = []
    for f in pr_files:
        patch = f.get("patch", "")
        if not patch:
            continue
        # Only scan added lines to avoid flagging pre-existing issues
        added = "\n".join(
            line[1:] for line in patch.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )
        if added.strip():
            findings = scan_file(added, f["filename"])
            all_findings.extend(findings)

    if not all_findings:
        return "No static security issues detected."
    import json
    return json.dumps(all_findings, indent=2)


_SYSTEM_PROMPT = """You are an expert code reviewer specializing in security, software design, and performance.

Your job: review a GitHub PR diff, identify issues, and post structured feedback.

Tools available:
- get_file_content: fetch full file for context beyond the diff
- search_codebase: find related symbols/patterns across the repo
- run_security_check: run static analysis on specific code
- add_inline_comment: queue a precise line-level comment (use liberally for specific issues)
- finalize_review: post the complete review (call exactly once when done)

Review process:
1. Read the full diff carefully
2. Use get_file_content for complex changes needing broader context
3. Add inline comments for line-specific findings (security bugs, logic errors, naming, style)
4. When done, call finalize_review with the verdict and a structured markdown summary

Summary format:
## Summary
[What changed, why it matters]

## Security Issues
[Findings with severity: CRITICAL/HIGH/MEDIUM/LOW]

## Code Quality
[Refactor suggestions, design issues, missing error handling]

## Verdict
[Why you chose APPROVE / REQUEST_CHANGES / COMMENT]

Verdict rules:
- REQUEST_CHANGES: any CRITICAL or HIGH security issue, data-loss bug, or broken API contract
- APPROVE: clean, correct, no blocking issues
- COMMENT: suggestions only, nothing blocking

Be specific and constructive. Cite exact line numbers in inline comments."""


def run_review() -> None:
    pr_number = int(os.environ["PR_NUMBER"])
    print(f"Fetching PR #{pr_number}…")

    pr_data = gh.get_pr(pr_number)
    pr_files = gh.get_pr_files(pr_number)
    head_sha = pr_data["head"]["sha"]

    init_state(pr_number, head_sha)

    diff_text = _build_diff(pr_files)
    security_text = _gather_security(pr_files)

    pr_title = pr_data["title"]
    pr_body = (pr_data.get("body") or "(no description)").strip()
    file_list = ", ".join(f["filename"] for f in pr_files[:30])
    total_files = len(pr_files)

    user_message = f"""## PR #{pr_number}: {pr_title}

**Description:** {pr_body}

**Changed files:** {total_files} total ({file_list}{', …' if total_files > 30 else ''})

---

## Diff (top {min(MAX_FILES, total_files)} files by size)

{diff_text}

---

## Static Security Scan (added lines only)

```json
{security_text}
```

Please review this PR now. Add inline comments for specific issues, then call finalize_review."""

    contents: list[types.Content] = [
        types.Content(role="user", parts=[types.Part(text=user_message)])
    ]

    model = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM_PROMPT,
        tools=[TOOL],
        temperature=0.2,
        max_output_tokens=8192,
    )

    for turn in range(1, MAX_TURNS + 1):
        print(f"Agent turn {turn}/{MAX_TURNS}…")

        response = _get_client().models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

        candidate = response.candidates[0]
        if not candidate.content or not candidate.content.parts:
            print("Empty response from model.", file=sys.stderr)
            break

        contents.append(candidate.content)

        fn_calls = [p for p in candidate.content.parts if getattr(p, "function_call", None)]

        if not fn_calls:
            # Model returned text without finalizing — extract and post as COMMENT
            text = next(
                (p.text for p in candidate.content.parts if getattr(p, "text", None)),
                "Review complete.",
            )
            s = get_state()
            if not s.finalized:
                print("Model stopped without finalize_review — posting text as COMMENT.")
                dispatch("finalize_review", {"summary": text, "verdict": "COMMENT"})
            break

        # Execute tool calls, collect responses
        fn_responses: list[types.Part] = []
        for part in fn_calls:
            fc = part.function_call
            args = dict(fc.args) if fc.args else {}
            preview = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items())
            print(f"  → {fc.name}({preview})")

            result = dispatch(fc.name, args)
            print(f"    ← {str(result)[:120]}")

            fn_responses.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name=fc.name,
                        response={"result": result},
                    )
                )
            )

        contents.append(types.Content(role="user", parts=fn_responses))

        if get_state().finalized:
            break

    s = get_state()
    if not s.finalized:
        print("ERROR: Max turns reached without finalization.", file=sys.stderr)
        sys.exit(1)

    print(f"Review posted. Verdict={s.verdict} inline_comments={len(s.inline_comments)}")


def main() -> None:
    run_review()


if __name__ == "__main__":
    main()
