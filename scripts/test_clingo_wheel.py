#!/usr/bin/env python3
"""
Verify the structure of a built clingo Pyodide wheel.

Usage:
    python3 scripts/test_clingo_wheel.py <wheel-dir-or-file>

Checks:
  - The .whl file exists and has a reasonable size (> 500 KB).
  - The file is a valid ZIP archive.
  - The archive contains a clingo shared library (.so).
  - The WHEEL metadata declares an emscripten platform tag.
"""

import pathlib
import sys
import zipfile

# A real clingo build is several MB; guard against empty/truncated output.
_MIN_SIZE = 500_000  # bytes


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: test_clingo_wheel.py <wheel-dir-or-file>", file=sys.stderr)
        sys.exit(1)

    target = pathlib.Path(sys.argv[1])
    if target.is_dir():
        whls = sorted(target.glob("*.whl"))
        if not whls:
            print(f"ERROR: no .whl file found in {target}", file=sys.stderr)
            sys.exit(1)
        whl = whls[0]
    else:
        whl = target

    print(f"Checking wheel: {whl.name}")

    # Check file size
    size = whl.stat().st_size
    print(f"  Size: {size / 1024 / 1024:.1f} MB")
    if size < _MIN_SIZE:
        print(
            f"ERROR: wheel is only {size} bytes — expected > {_MIN_SIZE} bytes",
            file=sys.stderr,
        )
        sys.exit(1)

    # Check it is a valid ZIP
    if not zipfile.is_zipfile(whl):
        print("ERROR: .whl is not a valid ZIP archive", file=sys.stderr)
        sys.exit(1)

    with zipfile.ZipFile(whl) as zf:
        names = zf.namelist()

        # Check for the clingo shared library
        so_files = [n for n in names if "clingo" in n.lower() and ".so" in n]
        if not so_files:
            print(
                "ERROR: no clingo shared library found in wheel.\n"
                "Contents:\n" + "\n".join(f"  {n}" for n in sorted(names)),
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"  Shared library : {so_files[0]}")

        # Check WHEEL metadata for emscripten platform tag
        wheel_metas = [n for n in names if n.endswith("/WHEEL")]
        if not wheel_metas:
            print(
                "ERROR: no WHEEL metadata file found.\n"
                "Contents:\n" + "\n".join(f"  {n}" for n in sorted(names)),
                file=sys.stderr,
            )
            sys.exit(1)
        wheel_content = zf.read(wheel_metas[0]).decode()
        if "emscripten" not in wheel_content.lower():
            print(
                "ERROR: WHEEL metadata does not contain an emscripten platform tag.\n"
                f"WHEEL content:\n{wheel_content}",
                file=sys.stderr,
            )
            sys.exit(1)
        tag_lines = [ln for ln in wheel_content.splitlines() if ln.startswith("Tag:")]
        for line in tag_lines:
            print(f"  {line}")

    print("Wheel structure: OK")


if __name__ == "__main__":
    main()
