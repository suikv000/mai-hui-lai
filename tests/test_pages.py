import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
class PagesBuildTests(unittest.TestCase):
    def test_public_allowlist_explicit_mode_and_repo_relative_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('tools/build_pages.py','static/index.html','static/app.js','static/style.css','data/products.json'):
                dest=root/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
            (root/'.env').write_text('PRIVATE_SENTINEL')
            (root/'runtime').mkdir();(root/'runtime/votes.sqlite3').write_text('PRIVATE_VOTES')
            result=subprocess.run(['python3',str(root/'tools/build_pages.py')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            out=root/'_site'
            self.assertEqual({p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file()}, {'index.html','app.js','style.css','data/products.json','.nojekyll'})
            html=(out/'index.html').read_text()
            self.assertIn('data-mode="static"',html)
            self.assertNotIn('data-mode="server"',html)
            self.assertNotIn('href="/',html);self.assertNotIn('src="/',html)
            self.assertEqual(len(json.loads((out/'data/products.json').read_text())['products']),38)
            for p in out.rglob('*'):
                if p.is_file():self.assertNotIn(b'PRIVATE_',p.read_bytes())

if __name__=='__main__':unittest.main()
