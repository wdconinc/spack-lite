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
    # 7. record.hh:160 uses <=> directly on field values whose types may not
    #    have operator<=> in Emscripten 3.1.46's libc++ sysroot (e.g.
    #    std::optional<std::variant<...>>, std::vector<std::pair<...>>, and
    #    std::span<const CppClingo::String> which has neither <=> nor <).
    #    Step 7a: inject an _em_compat::cmp3 helper at the top of record.hh
    #    (before any namespace) with three branches:
    #      1. Use <=> directly when the type supports it.
    #      2. Fall back to < when < is available (std::optional, std::vector).
    #      3. Fall back to element-wise <=> for ranges like std::span whose
    #         elements have <=> even though the container itself doesn't.
    #    fn_filter ensures we only touch record.hh.
    (
        'record.hh: add _em_compat::cmp3 helper for missing <=>',
        ('.hh', '.h'),
        'record.hh',
        '#pragma once\n',
        (
            '#pragma once\n'
            '#include <compare>\n'
            'namespace _em_compat {\n'
            'template<typename T>\n'
            'inline std::strong_ordering cmp3(T const &a, T const &b) {\n'
            '    if constexpr (requires { a <=> b; }) {\n'
            '        auto r = (a <=> b);\n'
            '        if (r < 0) return std::strong_ordering::less;\n'
            '        if (r > 0) return std::strong_ordering::greater;\n'
            '        return std::strong_ordering::equal;\n'
            '    } else if constexpr (requires { a < b; }) {\n'
            '        if (a < b) return std::strong_ordering::less;\n'
            '        if (b < a) return std::strong_ordering::greater;\n'
            '        return std::strong_ordering::equal;\n'
            '    } else if constexpr (requires {\n'
            '            a.begin(); a.end();\n'
            '            (*a.begin()) <=> (*a.begin()); }) {\n'
            '        auto _i1 = a.begin(), _e1 = a.end();\n'
            '        auto _i2 = b.begin(), _e2 = b.end();\n'
            '        for (; _i1 != _e1 && _i2 != _e2; ++_i1, ++_i2) {\n'
            '            auto _c = (*_i1) <=> (*_i2);\n'
            '            if (_c < 0) return std::strong_ordering::less;\n'
            '            if (_c > 0) return std::strong_ordering::greater;\n'
            '        }\n'
            '        if (_i1 == _e1 && _i2 == _e2) return std::strong_ordering::equal;\n'
            '        return _i1 == _e1 ?\n'
            '            std::strong_ordering::less : std::strong_ordering::greater;\n'
            '    } else {\n'
            '        return std::strong_ordering::equal;\n'
            '    }\n'
            '}\n'
            '} // namespace _em_compat\n'
        ),
    ),
    # 7b. Replace the direct <=> in Comp::compare with _em_compat::cmp3 so
    #     types without operator<=> use the < fallback defined above.
    (
        'record.hh: use _em_compat::cmp3 in Comp::compare instead of <=>',
        ('.hh', '.h'),
        'record.hh',
        'if (auto comp = a.template get_value<Tag>() <=> b.template get_value<Tag>(); comp != 0) {',
        'if (auto comp = ::_em_compat::cmp3(a.template get_value<Tag>(), b.template get_value<Tag>()); comp != 0) {',
    ),
    # 1d. 4-arg lexicographical_compare_three_way using 'elems_'/'x->elems_' names
    #     (used in lib/ground/src/term.cc).
    (
        'lexicographical_compare_three_way (elems_/x->elems_) → inline lambda',
        ('.hh', '.h', '.cc', '.cpp', '.cxx'),
        None,
        'std::lexicographical_compare_three_way(elems_.begin(), elems_.end(), x->elems_.begin(), x->elems_.end())',
        (
            '[&](){'
            'auto _b1=elems_.begin(),_e1=elems_.end();'
            'auto _b2=x->elems_.begin(),_e2=x->elems_.end();'
            'using _O=decltype(*_b1<=>*_b2);'
            'for(;_b1!=_e1&&_b2!=_e2;++_b1,++_b2)'
            'if(_O _c=(*_b1<=>*_b2);_c!=0)return _c;'
            'if(_b1==_e1&&_b2==_e2)return _O(0<=>0);'
            'return _b1==_e1?_O(-1<=>0):_O(1<=>0);'
            '}()'
        ),
    ),
    # 8. grounder.cc uses buf_.view() (std::ostringstream::view(), C++20, absent
    #    from Emscripten 3.1.46's libc++).  Replace with buf_.str().
    (
        '.view() → .str(): buf_ variant (grounder.cc)',
        ('.hh', '.h', '.cc', '.cpp', '.cxx'),
        None,
        'buf_.view()',
        'buf_.str()',
    ),
    # 9. ast.cc:1647: generic visitor lambda returns x <=> other.cast<...>() but
    #    different AST node types yield different ordering categories (strong vs
    #    partial), and the enclosing operator<=> has return type std::strong_ordering.
    #    Emscripten 3.1.46's libc++ lacks operator<=> for std::pair (returns
    #    partial_ordering) and doesn't implicit-convert partial→strong.
    #    Fix: explicit -> std::strong_ordering; use if constexpr to fall back to
    #    operator< for types whose <=> returns partial_ordering or is absent.
    (
        'ast.cc: visitor lambda → std::strong_ordering via if constexpr',
        ('.cc', '.cpp', '.cxx'),
        None,
        'return visit([&other](auto const &x) { return x <=> other.cast<std::decay_t<decltype(x)>>(); });',
        ('return visit([&other](auto const &x) -> std::strong_ordering {\n'
         '    using T = std::decay_t<decltype(x)>;\n'
         '    const auto &y = other.cast<T>();\n'
         '    if constexpr (requires { { x <=> y } -> std::convertible_to<std::strong_ordering>; }) {\n'
         '        return x <=> y;\n'
         '    } else {\n'
         '        if (x < y) return std::strong_ordering::less;\n'
         '        if (y < x) return std::strong_ordering::greater;\n'
         '        return std::strong_ordering::equal;\n'
         '    }\n'
         '});'),
    ),
    # 10. Disable LTO/IPO to avoid "Argument list too long" (E2BIG) in the
    #     Emscripten em++ link step.  When -flto=auto is active, Emscripten's
    #     Python wrapper expands all bitcode from .a archives into individual
    #     command-line arguments before calling the underlying LLVM linker,
    #     exceeding the OS ARG_MAX limit (~2 MB on Linux).
    #     Inject cmake code into the root CMakeLists.txt (after project()) to:
    #       (a) strip -flto flags from the cmake string-flag variables that the
    #           pyodide-build toolchain may have set, and
    #       (b) force CMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF (with CACHE FORCE)
    #           so cmake's own IPO mechanism does not re-add them for any target.
    #     fn_filter='CMakeLists.txt' limits candidates to CMakeLists.txt files;
    #     the regex anchors on 'project(clingo' so only the root file matches.
    (
        'CMakeLists.txt: disable LTO/IPO (E2BIG fix)',
        ('.txt',),
        'CMakeLists.txt',
        r'(?si)(project\s*\(\s*clingo[^)]*\))',
        (r'\1\n'
         r'# Emscripten compat: disable LTO/IPO to prevent E2BIG in em++ link.\n'
         r'# The pyodide-build toolchain injects -flto=auto into cmake flag\n'
         r'# variables; strip it here (CMakeLists.txt runs after the toolchain)\n'
         r'# and force IPO off so no target re-enables it.\n'
         r'foreach(_em_lto_v CMAKE_CXX_FLAGS CMAKE_CXX_FLAGS_RELEASE\n'
         r'        CMAKE_SHARED_LINKER_FLAGS CMAKE_MODULE_LINKER_FLAGS\n'
         r'        CMAKE_EXE_LINKER_FLAGS)\n'
         r'  if(DEFINED ${_em_lto_v})\n'
         r'    string(REGEX REPLACE "-flto[^ ]*" "" ${_em_lto_v} "${${_em_lto_v}}")\n'
         r'  endif()\n'
         r'endforeach()\n'
         r'set(CMAKE_INTERPROCEDURAL_OPTIMIZATION OFF CACHE BOOL "" FORCE)\n'
         r'set(CMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE OFF CACHE BOOL "" FORCE)'),
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
# auditwheel_emscripten (pulled in by pyodide-build) imports wheel.cli.pack
# which was removed in wheel>=0.44.  Pin wheel<0.44 so the import succeeds.
pip install --quiet "pyodide-build==${PYODIDE_VERSION}" "wheel<0.44"

# ---------------------------------------------------------------------------
# Step 2b: Patch pyodide_build/pywasmcross.py to use response files.
#           pyodide-build's compiler shim (handle_command) calls
#           subprocess.run(new_args) where new_args[0]='em++'.  When
#           clingo is linked, pyodide-build expands .a archives into hundreds
#           of individual WASM bitcode files; the resulting arg list can exceed
#           Linux's ARG_MAX (~2 MB), causing execve to fail with E2BIG *before*
#           em++ is even invoked — so an em++ wrapper cannot help.
#           The fix is applied at the source of the problem: pywasmcross.py's
#           handle_command() is patched to write a @response-file and call
#           em++ with just '@file' instead of the full arg list.
#           Emscripten 3.1.46 supports response files via expand_response_files.
#           The patch is in-place; pypabuild.py copies pywasmcross.py into the
#           per-build tmpdir, so any copy made after this patch is also fixed.
# ---------------------------------------------------------------------------
log "Patching pyodide_build/pywasmcross.py for response-file support …"
python3 - <<'PYEOF'
import importlib.util, pathlib, sys

spec = importlib.util.find_spec("pyodide_build.pywasmcross")
if spec is None or spec.origin is None:
    print("  pyodide_build.pywasmcross not found — skipping patch")
    sys.exit(0)

src_path = pathlib.Path(spec.origin)
src = src_path.read_text()

OLD = "    result = subprocess.run(new_args)\n    return result.returncode"
NEW = """\
    # Use a response file when the arg list exceeds ARG_MAX to avoid E2BIG.
    # pyodide-build expands .a archives into individual bitcode paths before
    # calling em++; with large libraries this can exceed Linux's ~2 MB limit.
    # Emscripten 3.1.46 supports @file response files (expand_response_files).
    _arg_total = sum(len(a) + 1 for a in new_args[1:])
    if _arg_total > 400_000:
        import shlex as _shlex
        import tempfile as _tf
        with _tf.NamedTemporaryFile(
            mode='w', suffix='.rsp', delete=False, prefix='empp_rsp_'
        ) as _rf:
            for _a in new_args[1:]:
                # Emscripten reads response files with shlex.split, so args
                # containing spaces (e.g. EXPORTED_FUNCTIONS Python repr) must
                # be quoted; use shlex.quote for portable shell-safe quoting.
                _rf.write(_shlex.quote(_a) + '\\n')
            _rsp = _rf.name
        try:
            result = subprocess.run([new_args[0], '@' + _rsp])
        finally:
            try:
                os.unlink(_rsp)
            except Exception:
                pass
    else:
        result = subprocess.run(new_args)
    return result.returncode"""

if OLD not in src:
    print(f"  pattern not found in {src_path} — already patched or version mismatch")
    sys.exit(0)

src_path.write_text(src.replace(OLD, NEW, 1))
print(f"  patched {src_path}")
PYEOF

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
# Step 4c: Wrap em++ with a response-file shim to avoid E2BIG.
#          pyodide-build's compiler shim expands .a archives into individual
#          WASM bitcode files before calling em++; with many large archives
#          the resulting argument list can exceed Linux's ARG_MAX (~2 MB).
#          Emscripten 3.1.46 supports @file response files (expand_response_files
#          in tools/utils.py).  We replace the emsdk em++ in-place with a
#          thin Python wrapper that writes the argument list to a temp file
#          and calls the real em++ via '@file' when the list is large.
#          Replacing in-place (rather than a PATH-based shadow) ensures the
#          wrapper is called even when pyodide-build hard-codes the em++ path.
#
#          IMPORTANT: In emsdk 3.1.46, the 'em++' file is a Python launcher
#          that appends '.py' to sys.argv[0] to find the implementation.
#          If we back it up as 'em++.real', it would then look for
#          'em++.real.py' (which doesn't exist).  Instead, we find 'em++.py'
#          (the actual Python implementation in the same directory) and call
#          it directly from the wrapper — no backup needed.
# ---------------------------------------------------------------------------
EMPP_REAL="$(command -v em++)"
EMPP_DIR="$(dirname "${EMPP_REAL}")"
EMPP_PY="${EMPP_DIR}/em++.py"
log "Installing em++ response-file wrapper (em++: ${EMPP_REAL}, em++.py: ${EMPP_PY}) …"
if [[ ! -f "${EMPP_PY}" ]]; then
  log "WARNING: ${EMPP_PY} not found — skipping em++ wrapper (E2BIG may occur at link time)"
else
python3 - "${EMPP_REAL}" "${EMPP_PY}" << 'EMPP_WRAPPER_EOF'
import sys, os
real_empp = sys.argv[1]   # path to em++ launcher (to be replaced in-place)
empp_py   = sys.argv[2]   # path to em++.py (real Python implementation)
wrapper = '''\
#!/usr/bin/env python3
# em++ response-file wrapper — avoids E2BIG (Argument list too long).
# Calls em++.py directly; never invokes the original em++ launcher so the
# em++/em++.real -> em++.real.py bootstrap chain is bypassed entirely.
import sys, os, subprocess, tempfile, shlex
_PY = EMPP_PY_PLACEHOLDER
# Use a response file when the argument list exceeds this threshold to stay
# safely below the Linux ARG_MAX minus typical environment-variable overhead.
_RSP_THRESHOLD = 400_000  # bytes
_args = sys.argv[1:]
if sum(len(a) + 1 for a in _args) > _RSP_THRESHOLD:
    with tempfile.NamedTemporaryFile(
            mode='w', suffix='.rsp', delete=False,
            prefix='empp_rsp_') as _f:
        for _a in _args:
            # Emscripten reads response files with shlex.split, so args
            # containing spaces (e.g. EXPORTED_FUNCTIONS Python repr) must
            # be quoted; use shlex.quote for portable shell-safe quoting.
            _f.write(shlex.quote(_a) + '\\n')
        _rsp = _f.name
    try:
        _r = subprocess.run([sys.executable, _PY, '@' + _rsp], check=False)
        sys.exit(_r.returncode)
    finally:
        try:
            os.unlink(_rsp)
        except Exception:
            pass
else:
    os.execv(sys.executable, [sys.executable, _PY] + _args)
'''
wrapper = wrapper.replace('EMPP_PY_PLACEHOLDER', repr(empp_py))
with open(real_empp, 'w') as f:
    f.write(wrapper)
os.chmod(real_empp, 0o755)
print(f'[build_clingo_pyodide] em++ response-file wrapper installed at {real_empp}; calls {empp_py}')
EMPP_WRAPPER_EOF
fi

# ---------------------------------------------------------------------------
# Step 5: Build the wheel
# ---------------------------------------------------------------------------
log "Building clingo Pyodide wheel …"
mkdir -p "${OUTPUT_DIR}"
# Belt-and-suspenders: tell cmake to disable IPO/LTO via env var in case the
# build frontend respects CMAKE_ARGS (scikit-build-core does).  The primary
# fix is the CMakeLists.txt patch above; this guards against any frontend that
# re-enables LTO before the patch takes effect.
export CMAKE_ARGS="${CMAKE_ARGS:-} -DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF"
(
  cd "${CLINGO_REPO}"
  # --exports pyinit: export only the PyInit_clingo symbol.
  # Using the default "requested" would export ALL public C++ symbols from every
  # linked .a archive (potentially thousands of mangled names), producing a huge
  # Python-repr EXPORTED_FUNCTIONS string with internal spaces that breaks
  # Emscripten's shlex-based response-file parser when a @rsp file is used.
  pyodide build --outdir "${OUTPUT_DIR}" --exports pyinit
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
