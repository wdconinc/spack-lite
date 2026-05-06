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
import os, re, sys

root = sys.argv[1]

# ---------------------------------------------------------------------------
# Patch table: list of (desc, exts, fn_filter, old, new[, is_regex]) tuples.
# When is_regex is True, old is a compiled-regex pattern applied with
# re.sub(old, new, src, flags=re.DOTALL); otherwise plain str.replace.
# ---------------------------------------------------------------------------
patches = [
    # 1a. std::lexicographical_compare_three_way is absent from Emscripten
    #     3.1.46's libc++ sysroot headers.  Replace with an equivalent lambda
    #     that only uses the C++20 spaceship operator (which IS present).
    #     Variant using 'lhs'/'rhs' parameter names (used in clasp/potassco).
    (
        'lexicographical_compare_three_way (lhs/rhs) → inline lambda',
        ('.hh', '.h', '.cc', '.cpp', '.cxx'),
        None,
        'std::lexicographical_compare_three_way(lhs.begin(), lhs.end(), rhs.begin(), rhs.end())',
        (
            '[&](){'
            'auto _b1=lhs.begin(),_e1=lhs.end();'
            'auto _b2=rhs.begin(),_e2=rhs.end();'
            'using _O=decltype(*_b1<=>*_b2);'
            'for(;_b1!=_e1&&_b2!=_e2;++_b1,++_b2)'
            'if(_O _c=(*_b1<=>*_b2);_c!=0)return _c;'
            'if(_b1==_e1&&_b2==_e2)return _O(0<=>0);'
            'return _b1==_e1?_O(-1<=>0):_O(1<=>0);'
            '}()'
        ),
    ),
    # 1b. Same as 1a but using 'a'/'b' parameter names (used in record.hh).
    (
        'lexicographical_compare_three_way (a/b) → inline lambda',
        ('.hh', '.h', '.cc', '.cpp', '.cxx'),
        None,
        'std::lexicographical_compare_three_way(a.begin(), a.end(), b.begin(), b.end())',
        (
            '[&](){'
            'auto _b1=a.begin(),_e1=a.end();'
            'auto _b2=b.begin(),_e2=b.end();'
            'using _O=decltype(*_b1<=>*_b2);'
            'for(;_b1!=_e1&&_b2!=_e2;++_b1,++_b2)'
            'if(_O _c=(*_b1<=>*_b2);_c!=0)return _c;'
            'if(_b1==_e1&&_b2==_e2)return _O(0<=>0);'
            'return _b1==_e1?_O(-1<=>0):_O(1<=>0);'
            '}()'
        ),
    ),
    # 2. hash.hh uses std::string_view and std::string without including their
    #    headers.  Emscripten 3.1.46's libc++ does not pull them in transitively.
    #    Both includes MUST go before the namespace, so anchor on #pragma once
    #    (filename-filtered to hash.hh to avoid false matches).
    #    <string> is needed because value_hash(std::string const&) calls
    #    std::hash<std::string_view>{}(x) which requires a complete std::string
    #    type for the implicit std::string→std::string_view conversion.
    #    <filesystem> is needed, and std::hash<std::filesystem::path> must be
    #    added because Emscripten 3.1.46's libc++ does not provide this
    #    specialization (it falls through to the deleted __enum_hash<path>).
    (
        'hash.hh: add includes and std::hash<filesystem::path> specialization',
        ('.hh', '.h'),
        'hash.hh',
        '#pragma once\n',
        (
            '#pragma once\n'
            '#include <filesystem>\n'
            '#include <string>\n'
            '#include <string_view>\n'
            'namespace std {\n'
            'template<> struct hash<filesystem::path> {\n'
            '    size_t operator()(filesystem::path const& p) const noexcept {\n'
            '        return hash<string>{}(p.string());\n'
            '    }\n'
            '};\n'
            '} // namespace std\n'
        ),
    ),
    # 3. Several files call std::ostringstream::view() (C++20, absent from
    #    Emscripten 3.1.46's libc++).  Replace with str() which returns
    #    std::string — implicitly convertible to std::string_view at the call.
    #    Patch each known variable-name spelling separately.
    (
        '.view() → .str(): out_ variant (logger.hh)',
        ('.hh', '.h', '.cc', '.cpp', '.cxx'),
        None,
        'out_.view()',
        'out_.str()',
    ),
    (
        '.view() → .str(): oss variant (profile.hh)',
        ('.hh', '.h', '.cc', '.cpp', '.cxx'),
        None,
        'oss.view()',
        'oss.str()',
    ),
    # 4. clasp_output.cpp uses std::ostream but only has it forward-declared
    #    (via <iosfwd>).  Insert a full <ostream> include so that
    #    basic_ostream's member functions (operator<< etc.) are available.
    (
        'clasp_output.cpp: add #include <ostream>',
        ('.cpp',),
        None,
        '#include <clasp/cli/clasp_output.h>',
        '#include <ostream>\n#include <clasp/cli/clasp_output.h>',
    ),
    # 5. clasp_output.cpp: (*ostream << s) is used in bool context, which
    #    Emscripten 3.1.46's libc++ does not support here.  Separate the
    #    write from the return so no bool conversion is needed.
    (
        'clasp_output.cpp: ostream bool conversion → statement + return',
        ('.cpp',),
        None,
        'return (*static_cast<std::ostream*>(o) << s) ? s.size() : 0;',
        '(*static_cast<std::ostream*>(o) << s); return s.size();',
    ),
    # 6. std::ranges::join_view is absent from Emscripten 3.1.46's libc++.
    #    Replace the single join_view for-loop header with two nested for
    #    headers that produce identical iteration without join_view.  The
    #    original loop body's opening '{' stays as-is, so one closing '}'
    #    still terminates the (now inner) loop, and the outer loop ends
    #    naturally — i.e. the rest of the file requires no further change.
    (
        'clasp_output.cpp: join_view → nested for loops',
        ('.cpp',),
        None,
        'for (auto x : std::ranges::join_view(std::array{sum, SumView{last, last != nullptr}})) {',
        'for (auto& _jr : std::array{sum, SumView{last, last != nullptr}}) for (auto x : _jr) {',
    ),
    # 1c. 5-argument form of lexicographical_compare_three_way with an explicit
    #     comparator lambda (used in lib/ground/src/term.cc and theory_term.cc).
    #     The call spans two source lines so we use a regex with DOTALL to match
    #     regardless of indentation.
    (
        'lexicographical_compare_three_way (args_ 5-arg) → inline lambda',
        ('.hh', '.h', '.cc', '.cpp', '.cxx'),
        None,
        (
            r'std::lexicographical_compare_three_way\('
            r'args_\.begin\(\), args_\.end\(\), x->args_\.begin\(\), x->args_\.end\(\),'
            r'\s*\[\]\(auto const &a, auto const &b\) \{ return \*a <=> \*b; \}\)'
        ),
        (
            '[&](){'
            'auto _b1=args_.begin(),_e1=args_.end();'
            'auto _b2=x->args_.begin(),_e2=x->args_.end();'
            'using _O=decltype(*(*_b1)<=>*(*_b2));'
            'for(;_b1!=_e1&&_b2!=_e2;++_b1,++_b2)'
            '{if(_O _c=(*(*_b1)<=>*(*_b2));_c!=0)return _c;}'
            'if(_b1==_e1&&_b2==_e2)return _O(0<=>0);'
            'return _b1==_e1?_O(-1<=>0):_O(1<=>0);}()'
        ),
        True,  # is_regex
    ),
]

total = 0
for dirpath, _, filenames in os.walk(root):
    for fn in filenames:
        path = os.path.join(dirpath, fn)
        for entry in patches:
            desc, exts, fn_filter, old, new = entry[:5]
            is_regex = entry[5] if len(entry) > 5 else False
            if not fn.endswith(exts):
                continue
            if fn_filter is not None and fn != fn_filter:
                continue
            try:
                with open(path) as f:
                    src = f.read()
            except Exception:
                continue
            if is_regex:
                new_src = re.sub(old, new, src, flags=re.DOTALL)
                if new_src == src:
                    continue
            else:
                if old not in src:
                    continue
                new_src = src.replace(old, new)
            with open(path, 'w') as f:
                f.write(new_src)
            print(f"  [{desc}] patched: {path}")
            total += 1
if total == 0:
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
# Step 4b: Wrap ninja so the build keeps going on errors (-k 0).
#          cmake invokes the native build tool via the absolute path it finds
#          in PATH during configure; create a thin wrapper BEFORE configure so
#          cmake caches the wrapper path and uses it for all builds.
# ---------------------------------------------------------------------------
NINJA_REAL="$(command -v ninja)"
NINJA_WRAPPER_DIR="/tmp/ninja-keep-going"
mkdir -p "${NINJA_WRAPPER_DIR}"
cat > "${NINJA_WRAPPER_DIR}/ninja" << NINJA_EOF
#!/bin/bash
exec "${NINJA_REAL}" -k 0 "\$@"
NINJA_EOF
chmod +x "${NINJA_WRAPPER_DIR}/ninja"
export PATH="${NINJA_WRAPPER_DIR}:${PATH}"
log "Ninja wrapper installed (adds -k 0): ${NINJA_WRAPPER_DIR}/ninja"

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
