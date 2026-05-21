"""Gemini tool declarations + dispatch. All GitHub network calls are lazy via github_client."""
import json
from dataclasses import dataclass, field
from typing import Optional

from google.genai import types

from . import github_client as gh
from .security_scanner import scan_file

# ---------------------------------------------------------------------------
# Review state — accumulated during the agentic loop, flushed in finalize_review
# ---------------------------------------------------------------------------

@dataclass
class ReviewState:
    pr_number: int
    head_sha: str
    inline_comments: list = field(default_factory=list)
    finalized: bool = False
    verdict: str = ""
    summary: str = ""


state: Optional[ReviewState] = None


def init_state(pr_number: int, head_sha: str) -> ReviewState:
    global state
    state = ReviewState(pr_number=pr_number, head_sha=head_sha)
    return state


def get_state() -> ReviewState:
    assert state is not None, "init_state() not called"
    return state


# ---------------------------------------------------------------------------
# Tool declarations
# ---------------------------------------------------------------------------

TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="get_file_content",
            description=(
                "Fetch the full content of a file in the repository. "
                "Use when the diff lacks enough context to understand a change."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "path": types.Schema(type=types.Type.STRING, description="File path relative to repo root"),
                    "ref": types.Schema(type=types.Type.STRING, description="Git ref (branch, tag, SHA). Defaults to PR head."),
                },
                required=["path"],
            ),
        ),
        types.FunctionDeclaration(
            name="search_codebase",
            description="Search the codebase for a symbol or pattern to understand broader usage context.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "pattern": types.Schema(type=types.Type.STRING, description="Symbol name or search term"),
                },
                required=["pattern"],
            ),
        ),
        types.FunctionDeclaration(
            name="run_security_check",
            description="Run static security analysis (bandit + custom patterns) on a code snippet.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "code": types.Schema(type=types.Type.STRING, description="Source code to analyze"),
                    "filename": types.Schema(type=types.Type.STRING, description="Filename for language detection (e.g. app.py)"),
                },
                required=["code", "filename"],
            ),
        ),
        types.FunctionDeclaration(
            name="add_inline_comment",
            description=(
                "Queue an inline review comment on a specific line of a changed file. "
                "Use for precise, actionable feedback on exact lines. "
                "Only valid for lines present in the PR diff."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "path": types.Schema(type=types.Type.STRING, description="File path as it appears in the diff"),
                    "line": types.Schema(type=types.Type.INTEGER, description="Line number in the new version of the file"),
                    "body": types.Schema(type=types.Type.STRING, description="Comment text (markdown supported)"),
                },
                required=["path", "line", "body"],
            ),
        ),
        types.FunctionDeclaration(
            name="finalize_review",
            description=(
                "Post the complete review to GitHub and end the review session. "
                "Call this exactly once when you are done reviewing. "
                "Include all findings in the summary."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "summary": types.Schema(
                        type=types.Type.STRING,
                        description="Full review summary in markdown (Summary / Security / Code Quality / Verdict sections)",
                    ),
                    "verdict": types.Schema(
                        type=types.Type.STRING,
                        description="APPROVE if clean, REQUEST_CHANGES if blocking issues found, COMMENT for suggestions only",
                        enum=["APPROVE", "REQUEST_CHANGES", "COMMENT"],
                    ),
                },
                required=["summary", "verdict"],
            ),
        ),
    ]
)

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def dispatch(name: str, args: dict) -> str:
    s = get_state()
    try:
        if name == "get_file_content":
            content = gh.get_file_content(args["path"], args.get("ref", "HEAD"))
            return content[:8000] + ("\n[truncated]" if len(content) > 8000 else "")

        if name == "search_codebase":
            results = gh.search_code(args["pattern"])
            return json.dumps(results, indent=2) if results else "No results found."

        if name == "run_security_check":
            findings = scan_file(args["code"], args["filename"])
            return json.dumps(findings, indent=2) if findings else "No security issues found."

        if name == "add_inline_comment":
            s.inline_comments.append({
                "path": args["path"],
                "line": int(args["line"]),
                "body": args["body"],
                "side": "RIGHT",
            })
            return f"Queued inline comment: {args['path']}:{args['line']}"

        if name == "finalize_review":
            s.summary = args["summary"]
            s.verdict = args["verdict"]
            gh.post_review(
                pr_number=s.pr_number,
                commit_sha=s.head_sha,
                body=args["summary"],
                event=args["verdict"],
                comments=s.inline_comments,
            )
            s.finalized = True
            return "Review posted successfully."

        return f"Unknown tool: {name}"

    except Exception as exc:
        return f"Tool error [{name}]: {type(exc).__name__}: {exc}"
