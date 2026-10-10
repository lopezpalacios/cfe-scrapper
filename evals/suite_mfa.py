"""MFA suite: extraction from the real fixture + distractor rejection.

mfa_code.py gains a --html-file mode so the extraction logic is testable
without the mailbox.
"""
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
MFA = "/Users/newman/.hermes/skills/cf-invoice-harvest/scripts/mfa_code.py"
FIX = HERE / "fixtures" / "mfa_email.html"


def _run(html: Path) -> tuple:
    out = subprocess.run(["python3", MFA, "--html-file", str(html)],
                         capture_output=True, text=True)
    return out.returncode, out.stdout.strip(), out.stderr.strip()


def _fixture():
    rc, code, _ = _run(FIX)
    return code if rc == 0 else f"rc={rc}"


_DISTRACTORS = """<html><body>
<p>Tu c\u00f3digo: </p>
<p style="letter-spacing:8px; color:#006341;">123456</p>
<p style="color:#333333;">333333</p>
<p style="color:#006341;">006341</p>
<p style="color:#555555;">Este c\u00f3digo es v\u00e1lido por <strong>5 minutos</strong>.</p>
</body></html>"""


def _distractor():
    p = Path("/tmp/evals-mfa-distractor.html")
    p.write_text(_DISTRACTORS)
    rc, code, _ = _run(p)
    return code if rc == 0 else f"rc={rc}"


def _no_code():
    p = Path("/tmp/evals-mfa-nocode.html")
    p.write_text("<html><body><p>Nada aqu\u00ed</p></body></html>")
    rc, code, err = _run(p)
    return ("1", code == "" and "otp" in err)


WEIGHT = 15

CASES = [
    {"id": "fixture_exact", "points": 5,
     "fn": _fixture, "expect": "074888"},
    {"id": "distractor_colors", "points": 5,
     "fn": _distractor, "expect": "123456"},
    {"id": "no_code_errors_otp", "points": 5,
     "fn": _no_code, "expect": ("1", True)},
]
