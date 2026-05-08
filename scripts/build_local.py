#!/usr/bin/env python3
"""Build a local, file://-friendly version of the Spack-Lite web app.

Browsers block  new Worker('worker.js')  when the page is loaded from a
file:// URL because each file:// document is treated as a unique origin.
This script produces an index.html where the worker source is inlined as a
Blob URL, bypassing the restriction.

It also injects  const _LOCAL_BASE_URL  into the worker so that relative
asset fetches (shim_system.py, shell.py, clingo wheel, spack-lite.tar.gz)
resolve correctly against the output directory rather than the blob URL.

Usage
-----
    python3 scripts/build_local.py [--out-dir .worktree/local]

The generated file is written to  <out-dir>/index.html.  Open it with:

    file:///<out-dir>/index.html

"""

import argparse
import json
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

VERSION_PLACEHOLDER = '__SPACK_LITE_VERSION__'

WORKER_PLACEHOLDER = "const worker = new Worker('worker.js');"


def git_describe() -> str:
    """Return a version string from ``git describe``, or 'dev' as a fallback."""
    try:
        return subprocess.check_output(
            ['git', 'describe', '--tags', '--always', '--dirty'],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        import sys
        print('warning: git describe failed; using "dev" as version', file=sys.stderr)
        return 'dev'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        '--out-dir',
        default=str(REPO_ROOT / '.worktree' / 'local'),
        help='Output directory (default: .worktree/local)',
    )
    args = parser.parse_args()

    out_dir = pathlib.Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Absolute file:// base URL so the blob worker can resolve relative assets.
    base_url = out_dir.as_uri() + '/'

    src_dir = REPO_ROOT
    worker_src = (src_dir / 'worker.js').read_text(encoding='utf-8')
    index_src = (src_dir / 'index.html').read_text(encoding='utf-8')

    # Prepend the base-URL constant.  worker.js picks this up via:
    #   if (typeof _LOCAL_BASE_URL !== 'undefined') return _LOCAL_BASE_URL;
    patched_worker = f'const _LOCAL_BASE_URL = {json.dumps(base_url)};\n' + worker_src

    # Build the inline replacement for  new Worker('worker.js')
    worker_json = json.dumps(patched_worker)
    inline_worker = (
        f"const _workerBlob = new Blob([{worker_json}],\n"
        f"    {{ type: 'application/javascript' }});\n"
        f"const worker = new Worker(URL.createObjectURL(_workerBlob));"
    )

    if WORKER_PLACEHOLDER not in index_src:
        raise RuntimeError(
            f"Could not find expected placeholder in index.html:\n  {WORKER_PLACEHOLDER}"
        )

    patched_index = index_src.replace(WORKER_PLACEHOLDER, inline_worker, 1)
    patched_index = patched_index.replace(VERSION_PLACEHOLDER, git_describe(), 1)

    out_file = out_dir / 'index.html'
    out_file.write_text(patched_index, encoding='utf-8')
    print(f'Built: {out_file.as_uri()}')


if __name__ == '__main__':
    main()
