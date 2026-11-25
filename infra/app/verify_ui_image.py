"""Compare tracked UI resources with the image; skip API-only checkouts."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[2]
    ui = root / "apps" / "ui"
    if not ui.is_dir():
        print("SKIP: apps/ui is absent (API-only checkout)")
        return

    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "apps/ui"], cwd=root
    ).decode().split("\0")
    expected = {
        path: hashlib.sha256((root / path).read_bytes()).hexdigest()
        for path in paths
        if path and (root / path).is_file()
    }
    assert any(p.startswith("apps/ui/templates/") for p in expected), "No tracked UI templates"
    assert any(p.startswith("apps/ui/static/") for p in expected), "No tracked UI static resources"
    # Image-side checks use the same UI_ROOT and Jinja environment as the API.
    code = """
import hashlib, json
from pathlib import Path
from rag_api.adapters.rest.ui import UI_ROOT, templates
expected = json.loads(INPUT)
assert UI_ROOT == Path('/app/apps/ui'), UI_ROOT
for name, digest in expected.items():
    path = Path('/app') / name
    assert path.is_file(), f'Missing UI resource: {name}'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, f'Changed UI resource: {name}'
for name in templates.env.list_templates():
    templates.env.get_template(name)
print(f'PASS: {len(expected)} UI files match; all Jinja2 templates load')
""".replace("INPUT", repr(json.dumps(expected)))
    subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "-i", sys.argv[1], "python", "-"],
        input=code, text=True, check=True,
    )


if __name__ == "__main__":
    main()
