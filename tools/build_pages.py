#!/usr/bin/env python3
"""Build an explicit static showcase from a small public-file allowlist."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / '_site'
FILES = {'static/style.css': 'style.css', 'static/app.js': 'app.js', 'data/products.json': 'data/products.json'}

def build():
    if OUTPUT.is_symlink():
        raise RuntimeError('Refusing symlink output directory')
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir()
    source = (ROOT / 'static/index.html').read_text(encoding='utf-8')
    marker = 'data-mode="server"'
    if source.count(marker) != 1:
        raise RuntimeError('Expected one explicit server mode marker')
    source = source.replace(marker, 'data-mode="static"')
    (OUTPUT / 'index.html').write_text(source, encoding='utf-8')
    for src, dest in FILES.items():
        target = OUTPUT / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / src, target)
    (OUTPUT / '.nojekyll').touch()
    print('Built static showcase: _site (no backend, credentials or private votes)')

if __name__ == '__main__':
    build()
