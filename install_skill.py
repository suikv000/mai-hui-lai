#!/usr/bin/env python3
"""Install the self-contained skill to an explicit skill-parent directory."""
import argparse
from pathlib import Path
import shutil

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--target', required=True, help='Skill 父目录，例如 ~/.codex/skills；已有同名目录时拒绝覆盖')
    args=p.parse_args()
    source=Path(__file__).resolve().parent/'skills'/'mai-hui-lai'
    parent=Path(args.target).expanduser().resolve()
    dest=parent/'mai-hui-lai'
    if dest.exists() or dest.is_symlink():
        p.error('目标已存在；为保护已有技能，不执行覆盖。请选择其他目录或自行备份。')
    parent.mkdir(parents=True,exist_ok=True)
    shutil.copytree(source,dest)
    print(f'已安装：{dest}')
if __name__=='__main__':
    main()
