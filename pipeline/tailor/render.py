"""RenderCV PDF rendering via subprocess."""

import shutil
import subprocess
import sys
import time
from pathlib import Path
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def render_pdf(yaml_path: Path) -> Path:
    r = subprocess.run(
        ["uv", "run", "rendercv", "render", str(yaml_path)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        print(f"[ERROR] RenderCV:\n{r.stderr}", file=sys.stderr)
        sys.exit(1)
    rendercv_dir = yaml_path.parent / "rendercv_output"
    # Prefer the known naming convention: {name}_CV.pdf (spaces→underscores).
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    cv_name = data.get("cv", {}).get("name", yaml_path.stem)
    expected = rendercv_dir / f"{cv_name.replace(' ', '_')}_CV.pdf"
    if expected.exists():
        pdf = expected
    else:
        # Fallback: newest PDF, but only if written in the last 10 seconds
        # (RenderCV just ran — any older PDF is from a prior render).
        candidates = list(rendercv_dir.glob("*.pdf"))
        if candidates:
            pdf = max(candidates, key=lambda p: p.stat().st_mtime)
            age = time.time() - pdf.stat().st_mtime
            if age > 10:
                print(f"[ERROR] Newest PDF is {age:.0f}s old — likely stale.",
                      file=sys.stderr)
                sys.exit(1)
        else:
            print("[ERROR] No PDF found in rendercv_output", file=sys.stderr)
            sys.exit(1)
    dst = yaml_path.parent / f"{yaml_path.stem}.pdf"
    shutil.copy2(pdf, dst)
    print(f"[INFO] PDF: {dst}", file=sys.stderr)
    return dst
