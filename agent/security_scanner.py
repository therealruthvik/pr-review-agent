"""Static security analysis: bandit + custom pattern checks."""
import json
import os
import re
import subprocess
import tempfile

# (name, compiled_pattern, severity)
_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("hardcoded_password",    re.compile(r'(?i)(password|passwd|pwd)\s*=\s*["\'][^"\']{4,}["\']'), "HIGH"),
    ("hardcoded_api_key",     re.compile(r'(?i)(api_key|apikey|secret_key)\s*=\s*["\'][^"\']{8,}["\']'), "HIGH"),
    ("hardcoded_token",       re.compile(r'(?i)\btoken\s*=\s*["\'][^"\']{8,}["\']'), "HIGH"),
    ("aws_access_key",        re.compile(r'AKIA[0-9A-Z]{16}'), "CRITICAL"),
    ("shell_injection",       re.compile(r'os\.system\s*\(|subprocess\.\w+\([^)]*shell\s*=\s*True'), "HIGH"),
    ("sql_injection_fstring", re.compile(r'f["\'].*?(SELECT|INSERT|UPDATE|DELETE).*?\{'), "HIGH"),
    ("pickle_load",           re.compile(r'\bpickle\.loads?\s*\('), "HIGH"),
    ("eval_exec",             re.compile(r'\b(eval|exec)\s*\('), "HIGH"),
    ("path_traversal",        re.compile(r'open\s*\([^)]*(\+|\.format\(|f["\'])'), "MEDIUM"),
    ("xml_external_entity",   re.compile(r'(etree|xml)\.(parse|fromstring)\s*\('), "MEDIUM"),
    ("assert_used",           re.compile(r'^\s*assert\s+'), "LOW"),
]


def _custom_checks(code: str, filename: str) -> list[dict]:
    findings = []
    for line_num, line in enumerate(code.splitlines(), 1):
        for name, pattern, severity in _PATTERNS:
            if pattern.search(line):
                findings.append({
                    "check": name,
                    "severity": severity,
                    "line": line_num,
                    "code": line.strip()[:120],
                    "file": filename,
                })
    return findings


def _bandit(code: str, filename: str) -> list[dict]:
    if not filename.endswith(".py"):
        return []
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, prefix="prreview_"
        ) as f:
            f.write(code)
            tmp_path = f.name

        result = subprocess.run(
            ["bandit", "-f", "json", "-q", tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.stdout:
            data = json.loads(result.stdout)
            return [
                {
                    "check": f"{i['test_id']}: {i['test_name']}",
                    "severity": i["issue_severity"],
                    "confidence": i["issue_confidence"],
                    "line": i["line_number"],
                    "code": i.get("code", "").strip()[:120],
                    "text": i["issue_text"],
                    "file": filename,
                }
                for i in data.get("results", [])
            ]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return []


def scan_file(code: str, filename: str) -> list[dict]:
    custom = _custom_checks(code, filename)
    bandit = _bandit(code, filename)

    seen: set[tuple] = set()
    merged: list[dict] = []
    for f in custom + bandit:
        key = (f.get("line"), f.get("check"))
        if key not in seen:
            seen.add(key)
            merged.append(f)

    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    return sorted(merged, key=lambda x: (severity_order.get(x.get("severity", "LOW"), 9), x.get("line", 0)))
