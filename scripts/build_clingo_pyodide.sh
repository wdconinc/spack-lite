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
#
# What this script does:
#   1. Clones (or reuses) the clingo source at CLINGO_BRANCH.
#   2. Installs pyodide-build at the matching PYODIDE_VERSION so that the
#      cross-compilation environment (Emscripten SDK) is automatically
#      downloaded and configured.
#   3. Runs `pyodide build` inside the clingo source tree.
#   4. Copies the resulting .whl to OUTPUT_DIR.
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
  git -C "${CLINGO_REPO}" submodule update --init --depth 1
else
  log "Using existing clingo source at ${CLINGO_REPO}"
  # Ensure we are on the correct branch and the tree is up to date.
  # Omit --depth 1 on fetch so it works regardless of local history depth.
  git -C "${CLINGO_REPO}" fetch origin "${CLINGO_BRANCH}"
  git -C "${CLINGO_REPO}" checkout "${CLINGO_BRANCH}"
  # Always refresh submodules so missing content doesn't cause build failures.
  git -C "${CLINGO_REPO}" submodule update --init --depth 1
fi

# ---------------------------------------------------------------------------
# Step 2: Install pyodide-build at the version matching our Pyodide target
#         so it downloads the correct Emscripten SDK automatically.
# ---------------------------------------------------------------------------
log "Installing pyodide-build==${PYODIDE_VERSION} …"
pip install --quiet "pyodide-build==${PYODIDE_VERSION}"

# Download (or reuse a cached) cross-compilation environment that bundles the
# Emscripten SDK pinned to what Pyodide ${PYODIDE_VERSION} was built with.
log "Setting up Pyodide cross-build environment for Pyodide ${PYODIDE_VERSION} …"
pyodide xbuildenv install --version "${PYODIDE_VERSION}"

# ---------------------------------------------------------------------------
# Step 3: Build the wheel
# ---------------------------------------------------------------------------
log "Building clingo Pyodide wheel …"
mkdir -p "${OUTPUT_DIR}"
(
  cd "${CLINGO_REPO}"
  pyodide build --output-directory "${OUTPUT_DIR}"
)

# ---------------------------------------------------------------------------
# Step 4: Report what we produced
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
