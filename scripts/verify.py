from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    env={'PYTHONPATH':str(ROOT/'src')}
    result=subprocess.run([sys.executable,'-m','pytest','-q'],cwd=ROOT,env={**__import__('os').environ,**env},check=False)
    manifest=json.loads((ROOT/'config/generated/graphene-coverage-manifest.json').read_text())
    print(json.dumps({'pytestExitCode':result.returncode,'exportedPoints':manifest['exportedPointCount'],'extensions':manifest['extensionPointCount'],'totalPoints':len(manifest['points'])},indent=2))
    raise SystemExit(result.returncode)
if __name__=='__main__':main()
