"""Barcode suite: real CFE bills + degraded variants decode to the right RPU.

Contract tested here:
- exact decode on two real fixtures (5 pts each);
- a realistic low-res crop of the barcode region (2.5 pts);
- degradations (gray, heavy JPEG, mid-DPI) still decode (2.5 each);
- a destroyed 72-DPI full-page raster must NEVER return a wrong RPU — it may
  decode or fail clean, but a wrong value is a safety bug (2.5 pts).
"""
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIX1 = HERE / "fixtures" / "cfe_006811122325.pdf"
FIX2 = HERE / "fixtures" / "cfe_008241008461.pdf"
CROP = HERE / "fixtures" / "cfe_006811122325_barcode_crop.png"
DECODE = "/Users/newman/.hermes/skills/cf-invoice-harvest/scripts/decode_rpu.sh"
WORK = Path("/tmp/evals-barcode")
WORK.mkdir(exist_ok=True)


def _decode(pdf: Path) -> str:
    out = subprocess.run(["bash", DECODE, str(pdf)], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


def _variant(name: str, build) -> Path:
    p = WORK / name
    if not p.exists():
        build(p)
    return p


def _v_r72(dst):
    subprocess.run(["pdftoppm", "-r", "72", "-png", "-singlefile",
                    str(FIX1), str(dst.with_suffix(""))], check=True, capture_output=True)
    dst.with_suffix(".png").rename(dst)


def _v_gray(dst):
    subprocess.run(["pdftoppm", "-r", "200", "-gray", "-png", "-singlefile",
                    str(FIX1), str(dst.with_suffix(""))], check=True, capture_output=True)
    dst.with_suffix(".png").rename(dst)


def _v_jpeg30(dst):
    subprocess.run(["pdftoppm", "-r", "200", "-png", "-singlefile",
                    str(FIX1), str(dst.with_suffix(""))], check=True, capture_output=True)
    png = dst.with_suffix(".png")
    subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "30",
                    str(png), "--out", str(dst)], check=True, capture_output=True)
    png.unlink(missing_ok=True)


def _v_r100(dst):
    subprocess.run(["pdftoppm", "-r", "100", "-png", "-singlefile",
                    str(FIX1), str(dst.with_suffix(""))], check=True, capture_output=True)
    dst.with_suffix(".png").rename(dst)


def _r72_safe():
    """Destroyed signal: decode-or-clean-fail is fine, a wrong RPU is not."""
    got = _decode(_variant("f1_r72.png", _v_r72))
    return "SAFE" if got in ("", "006811122325") else got


WEIGHT = 20

CASES = [
    {"id": "fixture1_exact", "points": 5,
     "fn": lambda: _decode(FIX1), "expect": "006811122325"},
    {"id": "fixture2_exact", "points": 5,
     "fn": lambda: _decode(FIX2), "expect": "008241008461"},
    {"id": "crop_lowres", "points": 2.5,
     "fn": lambda: _decode(CROP), "expect": "006811122325"},
    {"id": "fixture1_gray", "points": 2.5,
     "fn": lambda: _decode(_variant("f1_gray.png", _v_gray)), "expect": "006811122325"},
    {"id": "fixture1_jpeg30", "points": 2.5,
     "fn": lambda: _decode(_variant("f1_jpeg30.jpg", _v_jpeg30)), "expect": "006811122325"},
    {"id": "fixture1_r100", "points": 2.5,
     "fn": lambda: _decode(_variant("f1_r100.png", _v_r100)), "expect": "006811122325"},
    {"id": "r72_never_wrong", "points": 2.5,
     "fn": _r72_safe, "expect": "SAFE"},
]
