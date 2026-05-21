"""Pre-run preflight checks. Exits 1 on any failure. Run before every deploy."""
import glob
import importlib
import os
import re
import subprocess
import sys

REQUIRED_ENV = ["GEMINI_API_KEY", "GITHUB_TOKEN", "PR_NUMBER", "REPO"]
DEPRECATED_MODELS = {"gemini-pro", "gemini-1.0-pro", "gemini-1.5-pro-001", "gemini-ultra"}
# Matches module-level (column 0) client instantiation — lazy init means these must NOT appear
_TOPLEVEL_CLIENT_RE = re.compile(
    r'^[a-zA-Z_]\w*\s*=\s*(genai\.Client|Github|requests\.Session)\s*\(', re.MULTILINE
)

failures = 0


def check(label: str, ok: bool, detail: str = "") -> bool:
    global failures
    status = "PASS" if ok else "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    if not ok:
        failures += 1
    return ok


def main() -> None:
    print("=== Preflight checks ===\n")

    # 1. Required env vars
    print("-- Environment --")
    for var in REQUIRED_ENV:
        val = os.environ.get(var, "")
        check(f"ENV:{var}", bool(val), "missing" if not val else "")

    # 2. Model name not deprecated
    print("\n-- Model --")
    model = os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash"
    check("MODEL_NOT_DEPRECATED", model not in DEPRECATED_MODELS, f"using: {model}")

    # 3. Key imports
    print("\n-- Imports --")
    for module_path in ["google.genai", "requests", "bandit"]:
        try:
            importlib.import_module(module_path.split(".")[0])
            if "." in module_path:
                importlib.import_module(module_path)
            check(f"IMPORT:{module_path}", True)
        except ImportError as exc:
            check(f"IMPORT:{module_path}", False, str(exc))

    # 4. Bandit CLI
    print("\n-- Tools --")
    r = subprocess.run(["bandit", "--version"], capture_output=True, text=True)
    check("BANDIT_CLI", r.returncode == 0, r.stderr.strip() if r.returncode else "")

    # 5. Syntax check all agent source files
    print("\n-- Syntax --")
    for py_file in sorted(glob.glob("agent/*.py")):
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", py_file],
            capture_output=True, text=True,
        )
        check(f"SYNTAX:{py_file}", r.returncode == 0, r.stderr.strip())

    # 6. No top-level client instantiation (lazy init required)
    print("\n-- Lazy init --")
    for py_file in sorted(glob.glob("agent/*.py")):
        with open(py_file) as f:
            src = f.read()
        ok = not _TOPLEVEL_CLIENT_RE.search(src)
        check(f"LAZY_INIT:{py_file}", ok, "module-level client found" if not ok else "")

    # 7. Ignore file exists
    print("\n-- Ignore files --")
    check("GITIGNORE_EXISTS", os.path.isfile(".gitignore"), "missing")

    # 8. No deprecated model strings in source/CI
    print("\n-- Deprecated identifiers --")
    deprecated_found = []
    for pattern in ["**/*.py", ".github/**/*.yml"]:
        for fpath in glob.glob(pattern, recursive=True):
            if os.path.abspath(fpath) == os.path.abspath(__file__):
                continue  # skip self — DEPRECATED_MODELS definition would self-trigger
            try:
                with open(fpath) as f:
                    content = f.read()
                for dep in DEPRECATED_MODELS:
                    if dep in content:
                        deprecated_found.append(f"{fpath}: {dep}")
            except (OSError, UnicodeDecodeError):
                pass
    check("NO_DEPRECATED_MODEL_STRINGS", not deprecated_found, "; ".join(deprecated_found))

    # Summary
    print(f"\n{'='*40}")
    if failures:
        print(f"PREFLIGHT FAILED — {failures} check(s) failed. Fix before running.")
        sys.exit(1)
    print("PREFLIGHT PASSED — safe to run.")


if __name__ == "__main__":
    main()
