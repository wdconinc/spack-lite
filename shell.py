"""
shell.py — Python-backed POSIX-like shell for Spack-Lite

Provides a single public entry-point:

    result_json = run_shell_command(line)   # returns a JSON string

The JSON object contains:
    {
        "output": "<stdout of the pipeline>",
        "cwd":    "<display-form of the current working directory>"
    }

Supported built-ins
-------------------
  echo, pwd, cd, ls, cat, head, tail, grep,
  mkdir, rm, cp, mv, env, which, find, nano, spack

Variable assignment
-------------------
Bare ``NAME=VALUE`` tokens (or multiple leading assignments before a
command name) are written directly into os.environ.  No ``export``
keyword is needed:

    SPACK_VIEW=/my/view
    CC=gcc spack spec zlib

Pipeline (|) support
--------------------
Commands are chained by splitting on unquoted pipe characters.  Each
stage's stdout becomes the next stage's stdin.

Variable expansion
------------------
$VAR and ${VAR} are expanded from os.environ before each token is
passed to the handler.

This file is served alongside index.html / worker.js.  worker.js
fetches it over HTTP and executes it with pyodide.runPythonAsync().
"""

import fnmatch
import io
import json
import os
import shlex
import shutil
import sys

# ---------------------------------------------------------------------------
# Internal StringIO subclass that satisfies Spack's TTY-detection calls
# ---------------------------------------------------------------------------

class _ShellBuffer(io.StringIO):
    """StringIO that provides fileno()/isatty() so Spack's TTY-detection code
    does not raise io.UnsupportedOperation and abort the command."""

    def fileno(self):
        return 1  # pretend to be stdout; os.isatty(1) is False in Pyodide

    def isatty(self):
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _display_cwd():
    """Return the current directory, abbreviating $HOME as '~'."""
    cwd = os.getcwd()
    home = os.environ.get('HOME', '/home/pyodide')
    if cwd == home:
        return '~'
    if cwd.startswith(home + '/'):
        return '~' + cwd[len(home):]
    return cwd


def _split_pipeline(line):
    """Split *line* on unquoted '|' characters into stage strings.

    Correctly handles single-quoted strings, double-quoted strings, and
    backslash escapes outside single quotes so that a ``|`` inside a quoted
    context is never treated as a pipeline separator.
    """
    stages = []
    current = []
    in_single = False
    in_double = False
    escaped = False
    for ch in line:
        if escaped:
            # The previous character was an unquoted backslash; pass through
            # both the backslash (already appended) and this character.
            current.append(ch)
            escaped = False
        elif ch == '\\' and not in_single:
            # Backslash outside single quotes introduces an escape sequence.
            escaped = True
            current.append(ch)
        elif ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
        elif ch == '|' and not in_single and not in_double:
            stages.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    stages.append(''.join(current).strip())
    return [s for s in stages if s]


def _expand_vars(token):
    """Expand $VAR and ${VAR} references in *token* from os.environ."""
    import re
    def _replace(m):
        name = m.group(1) or m.group(2)
        return os.environ.get(name, '')
    return re.sub(r'\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)', _replace, token)


# Matches a shell variable assignment token: NAME=VALUE (value may be empty).
import re as _re
_ASSIGN_RE = _re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)')


# ---------------------------------------------------------------------------
# Built-in command implementations
# Each handler receives (args: list[str], stdin: str) and returns str.
# ---------------------------------------------------------------------------

def _cmd_echo(args, stdin):
    return ' '.join(args) + '\n'


def _cmd_pwd(args, stdin):
    return os.getcwd() + '\n'


def _cmd_cd(args, stdin):
    target = args[0] if args else os.environ.get('HOME', '/home/pyodide')
    # Support '~' and '~/...' shorthand
    home = os.environ.get('HOME', '/home/pyodide')
    if target == '~':
        target = home
    elif target.startswith('~/'):
        target = home + target[1:]
    try:
        os.chdir(target)
    except FileNotFoundError:
        return f'cd: {target}: No such file or directory\n'
    except NotADirectoryError:
        return f'cd: {target}: Not a directory\n'
    return ''


def _cmd_ls(args, stdin):
    show_all = False
    long_fmt = False
    paths = []
    for a in args:
        if a.startswith('-'):
            if 'a' in a:
                show_all = True
            if 'l' in a:
                long_fmt = True
        else:
            paths.append(a)
    if not paths:
        paths = [os.getcwd()]

    def _format_entry(name, full_path, use_long):
        """Return a single formatted line for *name* at *full_path*."""
        is_dir = os.path.isdir(full_path)
        if use_long:
            try:
                size = os.stat(full_path).st_size
            except OSError:
                size = 0
            mode = 'd' if is_dir else '-'
            return f'{mode}rwxr-xr-x  {size:>10d}  {name}{"/" if is_dir else ""}'
        return name + ('/' if is_dir else '')

    out = []
    for path in paths:
        try:
            entries = sorted(os.listdir(path))
            if not show_all:
                entries = [e for e in entries if not e.startswith('.')]
            for e in entries:
                out.append(_format_entry(e, os.path.join(path, e), long_fmt))
        except FileNotFoundError:
            out.append(f"ls: cannot access '{path}': No such file or directory")
        except NotADirectoryError:
            # path is a plain file — show it directly (respects -l)
            out.append(_format_entry(os.path.basename(path), path, long_fmt))
    return '\n'.join(out) + '\n' if out else ''


def _cmd_cat(args, stdin):
    if not args:
        return stdin
    out = []
    for f in args:
        try:
            with open(f, 'rb') as fh:
                out.append(fh.read().decode('utf-8', errors='replace'))
        except FileNotFoundError:
            out.append(f'cat: {f}: No such file or directory\n')
        except IsADirectoryError:
            out.append(f'cat: {f}: Is a directory\n')
    return ''.join(out)


def _cmd_head(args, stdin):
    n = 10
    files = []
    i = 0
    while i < len(args):
        if args[i] in ('-n', '--lines') and i + 1 < len(args):
            try:
                n = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif args[i].startswith('-') and args[i][1:].isdigit():
            n = int(args[i][1:])
            i += 1
        else:
            files.append(args[i])
            i += 1
    if not files:
        lines = stdin.splitlines(keepends=True)
        return ''.join(lines[:n])
    out = []
    for f in files:
        try:
            with open(f) as fh:
                out.append(''.join(fh.readlines()[:n]))
        except FileNotFoundError:
            out.append(f'head: {f}: No such file or directory\n')
    return ''.join(out)


def _cmd_tail(args, stdin):
    n = 10
    files = []
    i = 0
    while i < len(args):
        if args[i] in ('-n', '--lines') and i + 1 < len(args):
            try:
                n = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        elif args[i].startswith('-') and args[i][1:].isdigit():
            n = int(args[i][1:])
            i += 1
        else:
            files.append(args[i])
            i += 1
    if not files:
        lines = stdin.splitlines(keepends=True)
        return ''.join(lines[-n:])
    out = []
    for f in files:
        try:
            with open(f) as fh:
                out.append(''.join(fh.readlines()[-n:]))
        except FileNotFoundError:
            out.append(f'tail: {f}: No such file or directory\n')
    return ''.join(out)


def _cmd_grep(args, stdin):
    import re as _re
    case_insensitive = False
    invert = False
    show_line_numbers = False
    pattern = None
    files = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ('-i', '--ignore-case'):
            case_insensitive = True
            i += 1
        elif a in ('-v', '--invert-match'):
            invert = True
            i += 1
        elif a in ('-n', '--line-number'):
            show_line_numbers = True
            i += 1
        elif a.startswith('-'):
            i += 1  # skip unrecognised flags
        elif pattern is None:
            pattern = a
            i += 1
        else:
            files.append(a)
            i += 1
    if pattern is None:
        return 'grep: missing pattern\n'
    flags = _re.MULTILINE | (_re.IGNORECASE if case_insensitive else 0)
    try:
        regex = _re.compile(pattern, flags)
    except _re.error as exc:
        return f'grep: invalid regex: {exc}\n'

    def _match_lines(text, source=None):
        matched = []
        for lineno, line in enumerate(text.splitlines(), 1):
            hit = bool(regex.search(line))
            if invert:
                hit = not hit
            if hit:
                parts = []
                if source:
                    parts.append(source + ':')
                if show_line_numbers:
                    parts.append(str(lineno) + ':')
                parts.append(line)
                matched.append(''.join(parts))
        return '\n'.join(matched) + '\n' if matched else ''

    if not files:
        return _match_lines(stdin)
    out = []
    show_filename = len(files) > 1
    for f in files:
        try:
            with open(f) as fh:
                out.append(_match_lines(fh.read(), f if show_filename else None))
        except FileNotFoundError:
            out.append(f'grep: {f}: No such file or directory\n')
    return ''.join(out)


def _cmd_mkdir(args, stdin):
    if not args:
        return 'mkdir: missing operand\n'
    make_parents = False
    paths = []
    for a in args:
        if a in ('-p', '--parents'):
            make_parents = True
        elif not a.startswith('-'):
            paths.append(a)
    out = []
    for p in paths:
        try:
            if make_parents:
                os.makedirs(p, exist_ok=True)
            else:
                os.mkdir(p)
        except FileExistsError:
            if not make_parents:
                out.append(f"mkdir: cannot create directory '{p}': File exists\n")
        except FileNotFoundError:
            out.append(f"mkdir: cannot create directory '{p}': No such file or directory\n")
        except OSError as exc:
            out.append(f"mkdir: {p}: {exc.strerror}\n")
    return ''.join(out)


def _cmd_rm(args, stdin):
    if not args:
        return 'rm: missing operand\n'
    recursive = False
    force = False
    paths = []
    for a in args:
        if a.startswith('-'):
            if 'r' in a or 'R' in a:
                recursive = True
            if 'f' in a:
                force = True
        else:
            paths.append(a)
    out = []
    for p in paths:
        try:
            if os.path.isdir(p):
                if recursive:
                    shutil.rmtree(p)
                else:
                    out.append(f"rm: cannot remove '{p}': Is a directory\n")
            else:
                os.remove(p)
        except FileNotFoundError:
            if not force:
                out.append(f"rm: cannot remove '{p}': No such file or directory\n")
        except OSError as exc:
            out.append(f"rm: {p}: {exc.strerror}\n")
    return ''.join(out)


def _cmd_cp(args, stdin):
    recursive = False
    files = []
    for a in args:
        if a.startswith('-'):
            if 'r' in a or 'R' in a:
                recursive = True
        else:
            files.append(a)
    if len(files) < 2:
        return 'cp: missing destination file operand\n'
    src_list, dst = files[:-1], files[-1]
    out = []
    for src in src_list:
        try:
            if os.path.isdir(src):
                if not recursive:
                    out.append(f"cp: -r not specified; omitting directory '{src}'\n")
                    continue
                dest = os.path.join(dst, os.path.basename(src)) if os.path.isdir(dst) else dst
                shutil.copytree(src, dest)
            else:
                dest = os.path.join(dst, os.path.basename(src)) if os.path.isdir(dst) else dst
                shutil.copy2(src, dest)
        except FileNotFoundError:
            out.append(f"cp: '{src}': No such file or directory\n")
        except OSError as exc:
            out.append(f"cp: {exc.strerror}\n")
    return ''.join(out)


def _cmd_mv(args, stdin):
    files = [a for a in args if not a.startswith('-')]
    if len(files) < 2:
        return 'mv: missing destination file operand\n'
    src_list, dst = files[:-1], files[-1]
    out = []
    for src in src_list:
        try:
            dest = os.path.join(dst, os.path.basename(src)) if os.path.isdir(dst) else dst
            shutil.move(src, dest)
        except FileNotFoundError:
            out.append(f"mv: '{src}': No such file or directory\n")
        except OSError as exc:
            out.append(f"mv: {exc.strerror}\n")
    return ''.join(out)


def _cmd_env(args, stdin):
    if args:
        return f"env: extra arguments are not supported in this shell\n"
    return ''.join(f'{k}={v}\n' for k, v in sorted(os.environ.items()))


def _cmd_which(args, stdin):
    if not args:
        return 'which: missing argument\n'
    path_dirs = os.environ.get('PATH', '/usr/bin:/bin').split(':')
    out = []
    for name in args:
        # Shell built-ins take precedence over filesystem executables.
        if name in _BUILTINS:
            out.append(f'{name}: shell built-in\n')
            continue
        found = False
        for d in path_dirs:
            candidate = os.path.join(d, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                out.append(candidate + '\n')
                found = True
                break
        if not found:
            out.append(f'{name} not found\n')
    return ''.join(out)


def _cmd_find(args, stdin):
    root = '.'
    name_pattern = None
    type_filter = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == '-name' and i + 1 < len(args):
            name_pattern = args[i + 1]
            i += 2
        elif a == '-type' and i + 1 < len(args):
            type_filter = args[i + 1]
            i += 2
        elif not a.startswith('-'):
            root = a
            i += 1
        else:
            i += 1  # skip unrecognised options
    out = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for entry in sorted(dirnames) + sorted(filenames):
                full = os.path.join(dirpath, entry)
                is_dir = entry in dirnames
                # Apply -type filter
                if type_filter == 'f' and is_dir:
                    continue
                if type_filter == 'd' and not is_dir:
                    continue
                # Apply -name filter
                if name_pattern is not None and not fnmatch.fnmatch(entry, name_pattern):
                    continue
                # Normalise path (remove leading ./)
                display = full if not full.startswith('./') else full[2:]
                out.append(display)
    except (FileNotFoundError, NotADirectoryError):
        return f"find: '{root}': No such file or directory\n"
    return '\n'.join(out) + '\n' if out else ''


def _spack_python_is_interactive(rest):
    """Return True if 'spack python <rest>' would start an interactive REPL.

    CPython enters interactive mode when no arguments are given, or when -i is
    passed (which forces a REPL even after executing -c or a script).  Without
    -i, a -c snippet or positional script suppresses the REPL.
    """
    has_i = False
    has_code_or_script = False
    skip_next = False
    past_double_dash = False

    for arg in rest:
        if skip_next:
            skip_next = False
            continue
        if past_double_dash:
            has_code_or_script = True
            break
        if arg == '--':
            past_double_dash = True
        elif arg == '-i':
            has_i = True
        elif arg == '-c':
            has_code_or_script = True
            skip_next = True  # next arg is the code snippet, not a script
        elif arg.startswith('-c') and len(arg) > 2:
            has_code_or_script = True  # -cCODE combined form, no extra arg
        elif not arg.startswith('-'):
            has_code_or_script = True  # positional script

    return has_i or not has_code_or_script


# Match warning lines that contain only Spack's warning prefix and no message.
# Components: optional ANSI color codes, "==> Warning:", optional ANSI reset,
# optional trailing spaces, then end-of-line.
_EMPTY_SPACK_WARNING_LINE_RE = _re.compile(
    r'^(?:\x1b\[[0-9;]*m)*==>\s+Warning:(?:\x1b\[[0-9;]*m)*[ \t]*(?:\n|$)',
    _re.MULTILINE,
)


def _suppress_empty_spack_warnings(text):
    """Drop blank ``==> Warning:`` lines from spack output."""
    if not text:
        return text
    return _EMPTY_SPACK_WARNING_LINE_RE.sub('', text)


def _cmd_spack(args, stdin):
    """Route spack sub-commands through the Spack Python API."""
    try:
        import spack.main  # noqa: F401 — verify spack is importable
    except ImportError:
        return (
            "spack: not available — build spack-lite.tar.gz with\n"
            "  scripts/make_spack_lite.sh  and serve it alongside index.html\n"
        )

    if not args:
        return "Usage: spack <command> [options]\nTry 'spack help' for available commands.\n"

    # Find the actual subcommand — the first non-flag token in args.
    # Global flags like --debug/-d/-t/--backtrace may precede the subcommand.
    # Flags that consume the next token as a value are skipped with their arg.
    _VALUE_FLAGS = {'-c', '--config', '-C', '--config-scope',
                    '-e', '--env', '-D', '--env-dir', '--color'}
    _subcmd = None
    _i = 0
    while _i < len(args):
        _a = args[_i]
        if not _a.startswith('-'):
            _subcmd = _a
            break
        _i += 2 if _a in _VALUE_FLAGS else 1
    if _subcmd is None:
        return "Usage: spack <command> [options]\nTry 'spack help' for available commands.\n"

    # Interactive 'spack python' is not supported in the browser: Pyodide's
    # stdin fd does not support seek, so code.interact() raises ESPIPE.
    # Detect this case early and return a helpful message.
    if _subcmd == 'python' and _spack_python_is_interactive(args[_i + 1:]):
        return (
            "Interactive Python is not supported in browser mode.\n"
            "Use 'spack python -c \"<code>\"' to run Python code.\n"
            "Example: spack python -c \"import spack; print(spack.spack_version)\"\n"
        )

    # 'spack load-packages' triggers the on-demand fetch of spack-packages.tar.gz
    # which contains the full package universe.  The full set is NOT loaded
    # automatically to prevent memory exhaustion during `spack spec`.
    if _subcmd == 'load-packages':
        try:
            import js as _js
            if hasattr(_js, 'loadPackages'):
                _js.loadPackages()
                return (
                    "Loading full package set in background…\n"
                    "All Spack packages will be available shortly.\n"
                )
        except Exception:
            pass
        return (
            "spack load-packages: not available in this environment.\n"
            "(Requires the browser Pyodide context with spack-packages.tar.gz.)\n"
        )

    buf = _ShellBuffer()
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    old_stdin = sys.stdin
    try:
        sys.stdout = buf
        sys.stderr = buf
        # Replace sys.stdin with a StringIO so that any spack command that
        # reads from stdin (e.g. 'spack python -c …' using input()) receives
        # the piped input rather than the raw C-level fd, which in Pyodide
        # does not support seek and raises OSError(ESPIPE).
        sys.stdin = io.StringIO(stdin or '')
        # Use spack.main.main() — Spack's own CLI entry point — rather than
        # SpackCommand so that global flags (--debug, --backtrace, --verbose,
        # -c KEY=VAL, …) are parsed and applied automatically.
        import gc as _gc
        import spack.main as _spack_main
        # In the Pyodide/WASM environment, spack.util.parallel.ENABLE_PARALLELISM
        # defaults to True (``sys.platform != "win32"`` is True for "emscripten").
        # That causes both make_concurrent_executor() and imap_unordered() to attempt
        # real thread/process creation, which fails in the Pyodide thread-limited
        # context.  Force the sequential path for all parallel utilities when running
        # inside Pyodide (detected by the importability of the JS bridge module).
        try:
            import spack.util.parallel as _spack_parallel
            if _is_pyodide():  # noqa: F821 — defined by shim_system.py exec
                _spack_parallel.ENABLE_PARALLELISM = False
                # Patch clingo's async solve mode.  In Pyodide, pthread_create is
                # unavailable (no SharedArrayBuffer), so clingo.Control.solve(async_=True)
                # raises "thread constructor failed" at the C level.  The async solve is
                # used by spack/solver/asp.py to poll for KeyboardInterrupt; we replace
                # it with a context-manager shim that runs the solve synchronously.
                try:
                    from spack.solver.core import clingo as _get_clingo  # noqa: F401
                    _clingo_mod = _get_clingo()
                    _clingo_real_solve = _clingo_mod.Control.solve

                    class _SyncSolveHandle:
                        """Sync stand-in for clingo.SolveHandle used when async_=True."""

                        def __init__(self, ctrl, **kw):
                            self._ctrl = ctrl
                            self._kw = kw
                            self._result = None

                        def __enter__(self):
                            kw = {k: v for k, v in self._kw.items() if k != "async_"}
                            self._result = _clingo_real_solve(self._ctrl, **kw)
                            return self

                        def __exit__(self, *_):
                            pass

                        def wait(self, timeout=None):
                            return True  # already done

                        def cancel(self):
                            pass

                        def get(self):
                            return self._result

                    def _sync_clingo_solve(ctrl_self, *args, **kwargs):
                        if kwargs.get("async_", False):
                            return _SyncSolveHandle(ctrl_self, **kwargs)
                        return _clingo_real_solve(ctrl_self, *args, **kwargs)

                    _clingo_mod.Control.solve = _sync_clingo_solve
                except Exception:
                    pass
        except Exception:
            pass
        _orig_gc = _gc.get_threshold()
        try:
            _spack_main.main(args)
        except KeyboardInterrupt:
            import traceback as _tb
            # Show where the code was interrupted so the user can see which
            # part of the solver or package loading was hanging.
            buf.write('\nKeyboardInterrupt\nStack trace (interrupted at):\n')
            buf.write(''.join(_tb.format_stack()))
        finally:
            # spack.main.main() applies debug/backtrace/gc settings but never
            # restores them.  Reset here so they don't bleed into subsequent
            # commands.
            _gc.set_threshold(*_orig_gc)
            try:
                import llnl.util.tty as _tty
                _tty.set_debug(0)
                _tty.set_verbose(False)
                _tty.set_stacktrace(False)
            except Exception:
                pass
            try:
                import spack.error as _spack_err
                _spack_err.debug = False
                _spack_err.SHOW_BACKTRACE = False
            except Exception:
                pass
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        sys.stdin = old_stdin
        # Force a GC cycle to reclaim circular garbage from Spack's object
        # graph (Spec → Package → Dependency → Spec …) before the next
        # command runs.  In the Emscripten/WASM environment the heap can
        # only grow, so proactively freeing cycles reduces peak memory.
        import gc
        gc.collect()
        # Reset Spack's module-level caches so that package metadata loaded
        # for this command does not stay pinned for subsequent commands.
        try:
            import spack.config as _spack_cfg
            _spack_cfg.clear_caches()
        except Exception:
            pass
        try:
            import spack.repo as _spack_repo
            # Invalidate FastPackageChecker per-repo caches and the memoized
            # RepoPath.all_package_names result so packages added by
            # spack load-packages are visible to subsequent commands.
            for _r in _spack_repo.PATH.repos:
                if hasattr(_r, '_pkg_checker'):
                    _r._pkg_checker.invalidate()
            for _attr in ('_all_package_names', '_all_package_names_set'):
                _fn = getattr(_spack_repo.RepoPath, _attr, None)
                if callable(getattr(_fn, 'cache_clear', None)):
                    _fn.cache_clear()
        except Exception:
            pass
    return _suppress_empty_spack_warnings(buf.getvalue())


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

def _cmd_nano(args, stdin):
    """Stub editor — interactive editing is not available in browser mode."""
    target = args[0] if args else ''
    msg = (
        "nano: interactive editing is not available in browser mode.\n"
        "To view spack configuration use:  spack config get <section>\n"
        "To change a value use:            spack config add <key:value>\n"
    )
    if target:
        msg = f"nano: {target}: " + msg[6:]
    return msg


_BUILTINS = {
    'echo':  _cmd_echo,
    'pwd':   _cmd_pwd,
    'cd':    _cmd_cd,
    'ls':    _cmd_ls,
    'cat':   _cmd_cat,
    'head':  _cmd_head,
    'tail':  _cmd_tail,
    'grep':  _cmd_grep,
    'mkdir': _cmd_mkdir,
    'rm':    _cmd_rm,
    'cp':    _cmd_cp,
    'mv':    _cmd_mv,
    'env':   _cmd_env,
    'which': _cmd_which,
    'find':  _cmd_find,
    'nano':  _cmd_nano,
    'spack': _cmd_spack,
}


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def run_shell_command(line):
    """Parse and execute a shell pipeline.

    Returns a JSON string:
        {"output": "<stdout>", "cwd": "<display-cwd>"}
    """
    line = line.strip()
    if not line:
        return json.dumps({'output': '', 'cwd': _display_cwd()})

    stages = _split_pipeline(line)
    stdin_text = ''

    for stage in stages:
        stage = stage.strip()
        if not stage:
            continue
        try:
            try:
                raw_tokens = shlex.split(stage)
            except ValueError as exc:
                stdin_text = f'shell: parse error: {exc}\n'
                break

            if not raw_tokens:
                continue

            # Consume any leading NAME=VALUE assignment tokens (applying
            # variable expansion to the value), setting them in os.environ.
            # Variable expansion in the remaining command tokens happens
            # *after* assignments are set, so prefix assignments like
            # ``VAR=x cmd $VAR`` see the new value.
            idx = 0
            while idx < len(raw_tokens):
                m = _ASSIGN_RE.match(raw_tokens[idx])
                if m:
                    os.environ[m.group(1)] = _expand_vars(m.group(2))
                    idx += 1
                else:
                    break

            argv = [_expand_vars(tok) for tok in raw_tokens[idx:]]

            if not argv:
                # Pure assignment(s) — nothing left to execute.
                continue

            cmd_name = argv[0]
            cmd_args = argv[1:]
            handler = _BUILTINS.get(cmd_name)
            if handler is None:
                stdin_text = f'{cmd_name}: command not found\n'
            else:
                stdin_text = handler(cmd_args, stdin_text) or ''
        except KeyboardInterrupt:
            # Propagate Ctrl+C: let the REPL show the interrupt message already
            # written to buf inside _cmd_spack and stop the pipeline.
            break
        except Exception as exc:  # safety net: keep the shell alive if a handler raises unexpectedly
            stdin_text = f'Error in {stage!r}: {exc}\n'

    return json.dumps({'output': stdin_text, 'cwd': _display_cwd()})
