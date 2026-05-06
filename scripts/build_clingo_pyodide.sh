#!/usr/bin/env bash
# =============================================================================
# scripts/build_clingo_pyodide.sh
#
# Build a Pyodide (wasm32-emscripten) Python wheel for clingo from the
# upstream potassco/clingo wip-20 branch, which contains the necessary WASM
# support not yet in any official release.
#
# Usage:
#   bash scripts/build_clingo_pyodide.sh [CLINGO_REPO] [OUTPUT_DIR]
#
#   CLINGO_REPO   Path for the clingo source clone.    Default: /tmp/clingo-src
#   OUTPUT_DIR    Destination directory for the .whl.  Default: /tmp/clingo-wheel
#
# Environment variables:
#   CLINGO_BRANCH    Branch/tag of potassco/clingo to clone.  Default: wip-20
#   PYODIDE_VERSION  Pyodide version the wheel must target.   Default: 0.25.1
#   EMSDK_DIR        Directory for the Emscripten SDK clone.  Default: /tmp/emsdk
#
# What this script does:
#   1. Clones (or reuses) the clingo source at CLINGO_BRANCH.
#   2. Installs pyodide-build at the matching PYODIDE_VERSION.
#   3. Installs the Pyodide cross-build environment inside the clingo source
#      tree (pyodide-build resolves '.pyodide-xbuildenv' relative to CWD).
#   4. Installs the Emscripten SDK via emsdk at the version required by the
#      chosen Pyodide release (pyodide-build requires emcc in PATH).
#   5. Runs `pyodide build` inside the clingo source tree.
#   6. Copies the resulting .whl to OUTPUT_DIR.
#
# The produced wheel can be installed into a running Pyodide environment via
# micropip.install('<url>/clingo.whl') before Spack is imported, which
# satisfies Spack's bootstrap check (spack.bootstrap.core calls
# _python_import('clingo') and returns immediately if the import succeeds).
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CLINGO_REPO="${1:-/tmp/clingo-src}"
OUTPUT_DIR="${2:-/tmp/clingo-wheel}"
CLINGO_BRANCH="${CLINGO_BRANCH:-wip-20}"
PYODIDE_VERSION="${PYODIDE_VERSION:-0.25.1}"
# Directory where the Emscripten SDK (emsdk) will be cloned/cached.
# Override via environment variable to reuse an existing installation.
EMSDK_DIR="${EMSDK_DIR:-/tmp/emsdk}"

# ---------------------------------------------------------------------------
log() { echo "[build_clingo_pyodide] $*"; }

# ---------------------------------------------------------------------------
# Step 1: Ensure we have the clingo source tree
# ---------------------------------------------------------------------------
if [[ ! -d "${CLINGO_REPO}/.git" || ! -f "${CLINGO_REPO}/pyproject.toml" ]]; then
  log "Cloning potassco/clingo branch '${CLINGO_BRANCH}' into ${CLINGO_REPO} …"
  git clone --depth 1 --branch "${CLINGO_BRANCH}" \
      https://github.com/potassco/clingo.git "${CLINGO_REPO}"
  # The wip-20 repo uses git submodules for third-party deps (clasp, re2c, etc.)
  # --recursive is required so nested sub-submodules (e.g. clasp/libpotassco/amc)
  # are also initialised and CMake can find them at configure time.
  git -C "${CLINGO_REPO}" submodule update --init --recursive --depth 1
else
  log "Using existing clingo source at ${CLINGO_REPO}"
  # Ensure we are on the correct branch and the tree is up to date.
  # Omit --depth 1 on fetch so it works regardless of local history depth.
  git -C "${CLINGO_REPO}" fetch origin "${CLINGO_BRANCH}"
  git -C "${CLINGO_REPO}" checkout "${CLINGO_BRANCH}"
  # Always refresh submodules so missing content doesn't cause build failures.
  git -C "${CLINGO_REPO}" submodule update --init --recursive --depth 1
fi

# ---------------------------------------------------------------------------
# Step 1b: Patch clingo sources for Emscripten libc++ compatibility.
#          std::lexicographical_compare_three_way (C++20) is absent from the
#          cached sysroot headers in some Emscripten releases (e.g. 3.1.46).
#          Replace each call site with an equivalent lambda so the build
#          succeeds without altering clingo's logic.
# ---------------------------------------------------------------------------
log "Patching clingo sources for Emscripten libc++ compatibility …"
python3 - "${CLINGO_REPO}" <<'PYEOF'
import os, sys

root = sys.argv[1]
# Exact call pattern used in the clingo wip-20 headers.
OLD = 'std::lexicographical_compare_three_way(lhs.begin(), lhs.end(), rhs.begin(), rhs.end())'
# Equivalent C++20 lambda that does not rely on the missing stdlib function.
NEW = (
    '[&](){'
    'auto _b1=lhs.begin(),_e1=lhs.end();'
    'auto _b2=rhs.begin(),_e2=rhs.end();'
    'using _O=decltype(*_b1<=>*_b2);'
    'for(;_b1!=_e1&&_b2!=_e2;++_b1,++_b2)'
    'if(_O _c=(*_b1<=>*_b2);_c!=0)return _c;'
    'if(_b1==_e1&&_b2==_e2)return _O(0<=>0);'
    'return _b1==_e1?_O(-1<=>0):_O(1<=>0);'
    '}()'
)

count = 0
for dirpath, _, filenames in os.walk(root):
    for fn in filenames:
        if not fn.endswith(('.hh', '.h', '.cc', '.cpp', '.cxx')):
            continue
        path = os.path.join(dirpath, fn)
        try:
            with open(path) as f:
                src = f.read()
        except Exception:
            continue
        if OLD not in src:
            continue
        with open(path, 'w') as f:
            f.write(src.replace(OLD, NEW))
        print(f"  patched: {path}")
        count += 1
if count == 0:
    print("  no files needed patching")
PYEOF

# ---------------------------------------------------------------------------
# Step 2: Install pyodide-build at the version matching our Pyodide target.
# ---------------------------------------------------------------------------
log "Installing pyodide-build==${PYODIDE_VERSION} …"
pip install --quiet "pyodide-build==${PYODIDE_VERSION}"

# ---------------------------------------------------------------------------
# Step 3: Install the Pyodide cross-build environment inside the clingo
#         source tree.  pyodide-build resolves '.pyodide-xbuildenv' relative
#         to CWD, so it must live inside CLINGO_REPO when 'pyodide build' runs.
# ---------------------------------------------------------------------------
log "Setting up Pyodide cross-build environment for Pyodide ${PYODIDE_VERSION} …"
(cd "${CLINGO_REPO}" && pyodide xbuildenv install --download)

# ---------------------------------------------------------------------------
# Step 4: Install the Emscripten SDK.
#         The pyodide xbuildenv does NOT bundle the Emscripten toolchain —
#         pyodide-build requires 'emcc' to be available in PATH.
#         Read the required version from the xbuildenv's Makefile.envs so
#         this stays in sync automatically if PYODIDE_VERSION is bumped.
# ---------------------------------------------------------------------------
EMSCRIPTEN_VERSION=$(awk '/PYODIDE_EMSCRIPTEN_VERSION/ {print $NF}' \
    "${CLINGO_REPO}/.pyodide-xbuildenv/xbuildenv/pyodide-root/Makefile.envs")
log "Installing Emscripten ${EMSCRIPTEN_VERSION} (required by Pyodide ${PYODIDE_VERSION}) …"

if [[ ! -d "${EMSDK_DIR}/.git" ]]; then
  log "Cloning emsdk into ${EMSDK_DIR} …"
  git clone --depth 1 https://github.com/emscripten-core/emsdk.git "${EMSDK_DIR}"
fi
"${EMSDK_DIR}/emsdk" install "${EMSCRIPTEN_VERSION}"
"${EMSDK_DIR}/emsdk" activate "${EMSCRIPTEN_VERSION}"
# Source emsdk_env.sh to add emcc (and clang) to PATH.
# Temporarily relax strict-mode flags because emsdk_env.sh may reference
# variables that are unset in a non-login shell environment.
set +eu
# shellcheck disable=SC1091
source "${EMSDK_DIR}/emsdk_env.sh"
set -eu

# ---------------------------------------------------------------------------
# Step 5: Build the wheel
# ---------------------------------------------------------------------------
log "Building clingo Pyodide wheel …"
mkdir -p "${OUTPUT_DIR}"
(
  cd "${CLINGO_REPO}"
  pyodide build --outdir "${OUTPUT_DIR}"
)

# ---------------------------------------------------------------------------
# Step 6: Report what we produced
# ---------------------------------------------------------------------------
# Use nullglob so the array is empty (not literally '*.whl') when no file exists.
shopt -s nullglob
whls=("${OUTPUT_DIR}"/*.whl)
shopt -u nullglob
if [[ ${#whls[@]} -eq 0 ]]; then
  log "ERROR: no .whl found in ${OUTPUT_DIR}"
  exit 1
fi
WHL="${whls[0]}"
log "Wheel built successfully: ${WHL}"
SIZE=$(du -sh "${WHL}" | cut -f1)
log "Wheel size: ${SIZE}"
