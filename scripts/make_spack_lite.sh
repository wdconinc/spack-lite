#!/usr/bin/env bash
# =============================================================================
# scripts/make_spack_lite.sh
#
# Creates a stripped-down "Spack-Lite" distribution suitable for loading into
# a browser's Pyodide/MEMFS environment.
#
# Usage:
#   bash scripts/make_spack_lite.sh [SPACK_REPO] [OUTPUT_TARBALL] [OUTPUT_PKG_TARBALL]
#
#   SPACK_REPO          Path to (or URL of) a Spack clone.
#                       Defaults to: /tmp/spack-src
#   OUTPUT_TARBALL      Destination path for the produced core archive.
#                       Defaults to: spack-lite.tar.gz  (in the current directory)
#   OUTPUT_PKG_TARBALL  Destination path for the full packages archive loaded
#                       lazily in the browser background.
#                       Defaults to: spack-packages.tar.gz  (in the current directory)
#
# Environment variables:
#   SPACK_VERSION           Branch/tag of spack/spack to clone. Default: develop
#   PACKAGES_REPO           Path for the spack-packages clone. Default: /tmp/spack-packages-src
#   SPACK_PACKAGES_VERSION  Branch/tag of spack/spack-packages. Default: develop
#
# What this script does:
#   1. Clone (or use an existing clone of) the Spack core repository.
#   1b. Clone (or use an existing clone of) the spack-packages repository
#       (packages were moved from spack/spack to spack/spack-packages in
#       Spack's Package API v2.x).
#   2. Remove large/unnecessary subtrees:
#       - .git/           (version history, ~50 MB)
#       - lib/spack/docs/ and lib/spack/test/
#       - share/spack/docker/
#   3. Copy a seed set of builtin package recipes from spack-packages into
#      spack-lite.tar.gz (KEEP_PKGS below).  These are available immediately
#      when the browser interface becomes active.
#   3b. Copy ALL builtin package recipes into spack-packages.tar.gz.  This
#      archive is fetched lazily in the background so the full package set
#      becomes available without delaying the initial page load.
#   4. Inject the browser config files from spack_config/ into etc/spack/,
#      including repos.yaml which overrides the default git remote URL with
#      the local package path.
#   5. Pack the result into a .tar.gz with the top-level directory "spack/".
#
# Seed packages in spack-lite.tar.gz (adjust KEEP_PKGS to change the set):
#   autoconf automake bzip2 cmake compiler_wrapper curl diffutils expat
#   findutils gcc
#   gcc_runtime gdbm gettext glibc gmake hdf5 hwloc jsoncpp libaio libarchive libbsd
#   libffi libiconv libmng libpciaccess libsigsegv libtool libxml2 lz4 m4 mbedtls ncurses numactl
#   openblas openmpi openssl patch perl pkgconf python readline sqlite
#   tar util_linux xz zlib zstd
#
# Note: spack Package API v2 uses underscores in directory names, so
#   gcc-runtime → gcc_runtime   and   util-linux → util_linux.
# libgfortran is a virtual package provided by gcc_runtime (not a real
#   package directory); it must NOT appear in KEEP_PKGS.
# gmake provides the 'make' virtual package; many packages (including zlib)
#   depend on it as a build tool. Without it spack spec may fail with
#   UnknownPackageError for 'make' or similar build-tool virtuals.
#
# WARNING: Loading spack-packages.tar.gz (the full package archive) and then
#   running 'spack spec' causes the clingo solver to hang in the browser WASM
#   environment. The full archive (~7000 packages) generates a grounding
#   problem too large for WebAssembly. Keep KEEP_PKGS complete enough that
#   users never need to load the full archive for common operations.
#
# spack-packages.tar.gz contains all packages and is loaded lazily (on-demand).
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SPACK_REPO="${1:-/tmp/spack-src}"
OUTPUT_TARBALL="${2:-${REPO_ROOT}/spack-lite.tar.gz}"
OUTPUT_PKG_TARBALL="${3:-${REPO_ROOT}/spack-packages.tar.gz}"
PACKAGES_REPO="${PACKAGES_REPO:-/tmp/spack-packages-src}"
WORK_DIR="/tmp/spack-lite-build"
SPACK_LITE_DIR="${WORK_DIR}/spack"

# Packages to keep for the in-browser demo.
# Use underscore names matching spack Package API v2 directory names
# (gcc-runtime → gcc_runtime, util-linux → util_linux).
# libgfortran is a *virtual* package provided by gcc_runtime — it has no
# package directory of its own and must NOT appear in this list.
# glibc provides the 'libc' virtual package; without it spack spec fails with
# UnknownPackageError for 'libc'.
KEEP_PKGS=(
  autoconf automake bzip2 cmake curl diffutils expat findutils
  compiler_wrapper gcc gcc_runtime glibc gmake
  gdbm gettext hdf5 hwloc jsoncpp libaio libarchive libbsd libffi libiconv libmng libpciaccess
  libsigsegv libtool libxml2 lz4 m4 mbedtls ncurses numactl openblas openmpi
  openssl patch perl pkgconf python readline sqlite tar util_linux
  xz zlib zstd
)

SPACK_VERSION="${SPACK_VERSION:-develop}"

# ---------------------------------------------------------------------------
log() { echo "[make_spack_lite] $*"; }

# ---------------------------------------------------------------------------
# Step 1: Ensure we have a Spack source tree
# ---------------------------------------------------------------------------
if [[ ! -d "${SPACK_REPO}/.git" && ! -d "${SPACK_REPO}/lib/spack" ]]; then
  log "Cloning Spack ${SPACK_VERSION} into ${SPACK_REPO} …"
  git clone --depth 1 --branch "${SPACK_VERSION}" \
      https://github.com/spack/spack.git "${SPACK_REPO}"
else
  log "Using existing Spack source at ${SPACK_REPO}"
fi

# ---------------------------------------------------------------------------
# Step 1b: Ensure we have a spack-packages source tree (separate repo for
#          package recipes since Spack's new package API v2.x split packages
#          from the core)
# ---------------------------------------------------------------------------
SPACK_PACKAGES_VERSION="${SPACK_PACKAGES_VERSION:-develop}"
if [[ ! -d "${PACKAGES_REPO}/repos" ]]; then
  log "Cloning spack-packages ${SPACK_PACKAGES_VERSION} into ${PACKAGES_REPO} …"
  git clone --depth 1 --branch "${SPACK_PACKAGES_VERSION}" \
      https://github.com/spack/spack-packages.git "${PACKAGES_REPO}"
else
  log "Using existing spack-packages source at ${PACKAGES_REPO}"
fi

# ---------------------------------------------------------------------------
# Step 2: Set up a clean working copy
# ---------------------------------------------------------------------------
log "Setting up working directory at ${WORK_DIR} …"
rm -rf "${WORK_DIR}"
mkdir -p "${SPACK_LITE_DIR}"

# Rsync the essentials (skip .git, tests, docs, large binary blobs)
rsync -a --exclude='.git' \
         --exclude='lib/spack/docs' \
         --exclude='lib/spack/test' \
         --exclude='share/spack/docker' \
         --exclude='share/spack/qa' \
         --exclude='*.pyc' \
         --exclude='__pycache__' \
         "${SPACK_REPO}/" "${SPACK_LITE_DIR}/"

# ---------------------------------------------------------------------------
# Step 3: Populate the seed package set in spack-lite.tar.gz
#
#         KEEP_PKGS defines the packages that are available immediately when
#         the browser interface becomes active.  All other packages are loaded
#         lazily from spack-packages.tar.gz in the background.
#
#         Spack's new Package API v2.x stores packages in the separate
#         spack/spack-packages repository under repos/spack_repo/builtin/.
#         The path inside the tarball must contain "spack_repo/" to satisfy
#         Spack's v2 API directory-structure requirement (see spack/repo.py).
# ---------------------------------------------------------------------------
BUILTIN_SRC="${PACKAGES_REPO}/repos/spack_repo/builtin"
BUILTIN_DST="${SPACK_LITE_DIR}/var/spack/repos/spack_repo/builtin"
PKGS_DIR="${BUILTIN_DST}/packages"
ORIG_PKGS_DIR="${BUILTIN_SRC}/packages"

log "Populating seed package set — keeping ${#KEEP_PKGS[@]} packages …"
mkdir -p "${PKGS_DIR}"

# Copy build_systems, __init__.py, and repo.yaml from spack-packages — these
# are required for the v2.2 package API to load package classes correctly.
if [[ -d "${BUILTIN_SRC}/build_systems" ]]; then
  cp -r "${BUILTIN_SRC}/build_systems" "${BUILTIN_DST}/build_systems"
else
  log "ERROR: build_systems/ not found in ${BUILTIN_SRC} — aborting"
  exit 1
fi
for f in __init__.py repo.yaml; do
  if [[ -f "${BUILTIN_SRC}/${f}" ]]; then
    cp "${BUILTIN_SRC}/${f}" "${BUILTIN_DST}/${f}"
  else
    log "ERROR: ${f} not found in ${BUILTIN_SRC} — aborting"
    exit 1
  fi
done

# Packages required for compiler detection / spec concretization in the browser.
# These MUST be present in the spack-packages source or the build aborts.
# Note: use underscore-style names (Package API v2 convention).
REQUIRED_PKGS=(compiler_wrapper gcc gcc_runtime glibc)

for pkg in "${KEEP_PKGS[@]}"; do
  src="${ORIG_PKGS_DIR}/${pkg}"
  if [[ -d "${src}" ]]; then
    cp -r "${src}" "${PKGS_DIR}/${pkg}"
  else
    # Required packages are fatal; optional demo packages are only a warning.
    is_required=0
    for req in "${REQUIRED_PKGS[@]}"; do
      [[ "${pkg}" == "${req}" ]] && is_required=1 && break
    done
    if [[ "${is_required}" -eq 1 ]]; then
      log "ERROR: required package '${pkg}' not found in ${ORIG_PKGS_DIR} — aborting"
      exit 1
    else
      log "WARNING: package '${pkg}' not found in source — skipping"
    fi
  fi
done

# ---------------------------------------------------------------------------
# Step 4: Inject browser configuration
# ---------------------------------------------------------------------------
log "Injecting browser config files …"
CFG_SRC="${REPO_ROOT}/spack_config"
CFG_DST="${SPACK_LITE_DIR}/etc/spack"
mkdir -p "${CFG_DST}"

for f in config.yaml packages.yaml repos.yaml; do
  if [[ -f "${CFG_SRC}/${f}" ]]; then
    cp "${CFG_SRC}/${f}" "${CFG_DST}/${f}"
  fi
done

# ---------------------------------------------------------------------------
# Step 5: Remove __pycache__ and .pyc leftovers
# ---------------------------------------------------------------------------
find "${SPACK_LITE_DIR}" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "${SPACK_LITE_DIR}" -name '*.pyc' -delete 2>/dev/null || true

# ---------------------------------------------------------------------------
# Step 6: Pack the archive
# ---------------------------------------------------------------------------
log "Creating archive ${OUTPUT_TARBALL} …"
(cd "${WORK_DIR}" && tar -czf "${OUTPUT_TARBALL}" spack/)

SIZE=$(du -sh "${OUTPUT_TARBALL}" | cut -f1)
log "Core archive size: ${SIZE}"

# ---------------------------------------------------------------------------
# Step 7: Build spack-packages.tar.gz with ALL packages from spack-packages.
#         This archive is fetched lazily in the browser background so the full
#         package set becomes available without blocking the initial page load.
# ---------------------------------------------------------------------------
log "Building full packages archive ${OUTPUT_PKG_TARBALL} …"
PKG_WORK_DIR="/tmp/spack-packages-build"
PKG_BUILTIN_DST="${PKG_WORK_DIR}/spack/var/spack/repos/spack_repo/builtin"
rm -rf "${PKG_WORK_DIR}"
mkdir -p "${PKG_BUILTIN_DST}/packages"

# Copy all packages from the spack-packages source.
cp -r "${ORIG_PKGS_DIR}/." "${PKG_BUILTIN_DST}/packages/"

# Remove .pyc / __pycache__ artefacts from the packages archive.
find "${PKG_WORK_DIR}" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find "${PKG_WORK_DIR}" -name '*.pyc' -delete 2>/dev/null || true

(cd "${PKG_WORK_DIR}" && tar -czf "${OUTPUT_PKG_TARBALL}" spack/)
rm -rf "${PKG_WORK_DIR}"

PKG_SIZE=$(du -sh "${OUTPUT_PKG_TARBALL}" | cut -f1)
log "Packages archive size: ${PKG_SIZE}  (loaded lazily in the browser background)"

log "Place both '$(basename "${OUTPUT_TARBALL}")' and '$(basename "${OUTPUT_PKG_TARBALL}")'"
log "in the same directory as index.html"
log "Then serve the directory with a static HTTP server, e.g.:"
log "  python3 -m http.server 8080"
