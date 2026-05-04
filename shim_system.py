"""
shim_system.py — The "System Lie"

This module monkey-patches the Python standard library so that Spack
believes it is running on a standard Linux/x86_64 host even though the
actual runtime is a Pyodide/Emscripten WebAssembly environment inside a
browser where fork(2), exec(2), and most POSIX syscalls are unavailable.

Modules patched
---------------
1.  os / platform   — fake Linux x86_64 uname / machine / system identity
2.  subprocess      — shell-out calls delegated to wasm-git (git) or
                      return canned mock responses for everything else
3.  os.environ      — sane default environment variables
4.  filesystem      — required ~/.spack directory tree created up front
5.  grp / pwd       — group and password database stubs
6.  termios         — terminal-attribute constants and no-op control functions
7.  tty             — setraw / setcbreak no-ops (wraps termios)
8.  readline        — tab-completion stubs (used by `spack python`)
9.  fcntl           — file-locking no-ops; fcntl/ioctl raise ENOSYS/ENOTTY
10. ssl             — stub context / wrap_socket (browser handles TLS at JS layer)
11. lzma            — raises LZMAError on use (no WASM lzma available by default)
12. fake executables — stub files in /usr/bin so which_string() finds git/gcc/etc.
13. _multiprocessing — stub C extension; SemLock backed by threading primitives

Git operations
--------------
When wasm-git is available (loaded by worker.js as `self.gitCall`), git
subprocess calls are delegated to libgit2-compiled-to-WASM running in the
same Web Worker.  `git clone` results are automatically bridged from
wasm-git's MEMFS into Pyodide's MEMFS so Python can read the cloned files.
If wasm-git is not loaded (e.g. CDN unreachable) the shim falls back to
canned mock responses so that read-only spack commands still work.

This file is the **single canonical source** for all Pyodide compatibility
shims.  worker.js fetches it over HTTP and executes it at start-up; there
is no separate inline fallback.

Usage
-----
In the Web Worker (worker.js, the normal path):

    const resp = await fetch('shim_system.py');
    if (!resp.ok) throw new Error(`Failed to fetch shim_system.py (HTTP ${resp.status})`);
    await pyodide.runPythonAsync(await resp.text());

The file must be served from the same origin as index.html / worker.js.
It is **not** written into the Pyodide MEMFS, so ``open('shim_system.py')``
will not work inside Python — always pass the source text directly to
``runPythonAsync``.

For local testing outside Pyodide (standard CPython):

    exec(compile(open('/path/to/shim_system.py').read(), 'shim_system.py', 'exec'))
"""

import builtins
import sys
import os
import platform
import collections
import json as _json

# ---------------------------------------------------------------------------
# 1.  Platform identity — make Spack detect "Linux x86_64"
# ---------------------------------------------------------------------------
sys.platform = "linux"
os.name = "posix"


# os.uname() replacement
class _FakeUname:
    """Mimics the named-tuple returned by the real os.uname()."""

    sysname = "Linux"
    nodename = "spack-browser"
    release = "5.15.0-generic"
    version = "#1 SMP Mon Jan 01 00:00:00 UTC 2024"
    machine = "x86_64"

    def __iter__(self):
        return iter(
            [self.sysname, self.nodename, self.release, self.version, self.machine]
        )

    def __getitem__(self, idx):
        return list(self)[idx]

    def __repr__(self):
        return (
            f"posix.uname_result("
            f"sysname={self.sysname!r}, nodename={self.nodename!r}, "
            f"release={self.release!r}, version={self.version!r}, "
            f"machine={self.machine!r})"
        )


os.uname = lambda: _FakeUname()

# os.isatty — always False in a browser web-worker (no real TTY)
os.isatty = lambda fd: False

# platform module
platform.machine = lambda: "x86_64"
platform.system = lambda: "Linux"
platform.release = lambda: "5.15.0-generic"
platform.node = lambda: "spack-browser"
platform.processor = lambda: "x86_64"
platform.architecture = lambda bits="", linkage="": ("64bit", "ELF")

_UnameTuple = collections.namedtuple(
    "uname_result",
    ["system", "node", "release", "version", "machine", "processor"],
)
platform.uname = lambda: _UnameTuple(
    "Linux", "spack-browser", "5.15.0-generic", "#1 SMP", "x86_64", "x86_64"
)

# ---------------------------------------------------------------------------
# 2.  subprocess shim — intercept all shell-out calls
#
#     Git commands are delegated to wasm-git (libgit2 compiled to WASM)
#     via self.gitCall exposed by worker.js.  All other commands return
#     canned mock responses sufficient for spack's probing / version checks.
# ---------------------------------------------------------------------------
import subprocess  # noqa: E402 — must come after platform patching
from unittest.mock import MagicMock  # noqa: E402


def _build_mock_result(args=None, **kwargs):
    """Return a Mock CompletedProcess-like object with sensible stdout."""
    cmd = " ".join(map(str, args)) if isinstance(args, (list, tuple)) else str(args or "")

    stdout = b""
    stderr = b""
    exit_code = 0  # only overridden in the git branch below

    if "uname" in cmd:
        stdout = b"x86_64\n"
    elif "gcc" in cmd or ("cc" in cmd and "clang" not in cmd):
        stdout = b"gcc (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0\n"
    elif "g++" in cmd or "c++" in cmd:
        stdout = b"g++ (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0\n"
    elif "gfortran" in cmd or "fortran" in cmd:
        stdout = b"GNU Fortran (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0\n"
    elif "git" in cmd:
        # Delegate to wasm-git (libgit2 compiled to WASM) when available.
        # worker.js exposes self.gitCall(argsJson) which runs the real git
        # binary in wasm-git's sandbox and returns captured stdout.  For
        # `git clone` it also bridges the cloned tree into Pyodide's MEMFS.
        # Fall back to mock responses when wasm-git is not loaded (e.g. CDN
        # unreachable) so that read-only spack commands still work.
        git_args = list(args) if isinstance(args, (list, tuple)) else __import__("shlex").split(str(args or ""))
        # Strip the 'git' executable name — wasm-git's callMain is git itself.
        # The fake stub is installed at /usr/bin/git, so argv[0] may be a
        # full path; compare by basename to handle both 'git' and '/usr/bin/git'.
        if git_args and os.path.basename(git_args[0]) == "git":
            git_args = git_args[1:]
        _wasm_result = None
        try:
            import js as _js
            if hasattr(_js, "gitCall"):
                _wasm_result = _json.loads(_js.gitCall(_json.dumps(git_args)))
        except Exception:
            pass
        if _wasm_result is not None:
            stdout    = (_wasm_result.get("stdout") or "").encode()
            stderr    = (_wasm_result.get("stderr") or "").encode()
            exit_code = int(_wasm_result.get("returncode") or 0)
        elif "rev-parse" in cmd or "log" in cmd:
            stdout = b"abc1234\n"
        else:
            stdout = b"git version 2.34.1\n"
    elif "lscpu" in cmd:
        stdout = (
            b"Architecture: x86_64\n"
            b"CPU(s): 4\n"
            b"Thread(s) per core: 2\n"
            b"Core(s) per socket: 2\n"
            b"Socket(s): 1\n"
            b"Vendor ID: GenuineIntel\n"
            b"CPU family: 6\n"
            b"Model: 165\n"
            b"Model name: Intel(R) Core(TM) i7 CPU\n"
        )
    elif "clingo" in cmd:
        stdout = b"clingo version 5.6.2\n"
    elif "module" in cmd:
        # Environment-modules / Lmod — not available in browser
        stdout = b""
    elif "id" in cmd and len(cmd.strip()) <= 4:
        stdout = b"uid=1000(pyodide) gid=1000(pyodide) groups=1000(pyodide)\n"
    elif "make" in cmd:
        stdout = b"GNU Make 4.3\n"
    elif "cmake" in cmd:
        stdout = b"cmake version 3.22.1\n"

    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = exit_code
    result.args = args
    return result


class _MockPopen:
    """Thin replacement for subprocess.Popen that never forks."""

    def __init__(self, args=None, *extra_positional, **kwargs):
        r = _build_mock_result(args)
        self._stdout = r.stdout
        self._stderr = r.stderr
        self.returncode = 0
        self.pid = 99999

    # Attributes expected by Spack / distutils
    stdout = None
    stderr = None

    def communicate(self, input=None, timeout=None):  # noqa: A002
        return self._stdout, self._stderr

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def poll(self):
        return 0

    def kill(self):
        pass

    def terminate(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _mock_run(args=None, *extra, **kwargs):
    return _build_mock_result(args)


def _mock_check_output(args=None, *extra, **kwargs):
    return _build_mock_result(args).stdout


subprocess.run = _mock_run
subprocess.call = lambda *a, **kw: 0
subprocess.check_call = lambda *a, **kw: 0
subprocess.check_output = _mock_check_output
subprocess.Popen = _MockPopen

# ---------------------------------------------------------------------------
# 3.  os.environ defaults — give Spack a sane environment
# ---------------------------------------------------------------------------
os.environ.setdefault("HOME", "/home/pyodide")
os.environ.setdefault("USER", "pyodide")
os.environ.setdefault("LOGNAME", "pyodide")
os.environ.setdefault("SHELL", "/bin/sh")
os.environ.setdefault("PATH", "/usr/bin:/bin:/usr/local/bin")
os.environ.setdefault("SPACK_USER_CONFIG_PATH", "/home/pyodide/.spack")
os.environ.setdefault("SPACK_ROOT", "/home/pyodide/spack")

# ---------------------------------------------------------------------------
# 4.  Ensure required filesystem structure exists
#     (Only attempt this when running inside Pyodide/MEMFS where we own /home)
# ---------------------------------------------------------------------------
_REQUIRED_DIRS = [
    "/home/pyodide/.spack/linux",
    "/home/pyodide/.spack/darwin",
    "/tmp/spack-stage",
    "/tmp/spack-cache",
    "/proc",
]
for _d in _REQUIRED_DIRS:
    try:
        os.makedirs(_d, exist_ok=True)
    except (PermissionError, OSError):
        pass  # Silently skip on non-Pyodide hosts

# /proc/cpuinfo — archspec reads this file to identify the CPU microarchitecture.
# Provide a realistic x86_64 (Haswell) entry so archspec resolves a concrete
# target rather than falling through to slow/broken fallback paths.
_CPUINFO = """\
processor\t: 0
vendor_id\t: GenuineIntel
cpu family\t: 6
model\t\t: 60
model name\t: Intel(R) Core(TM) i5-4590 CPU @ 3.30GHz
stepping\t: 3
flags\t\t: fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov \
pat pse36 clflush mmx fxsr sse sse2 ss ht syscall nx pdpe1gb rdtscp lm \
constant_tsc rep_good nopl xtopology nonstop_tsc cpuid aperfmperf pni \
pclmulqdq ssse3 fma cx16 pcid sse4_1 sse4_2 x2apic movbe popcnt \
tsc_deadline_timer aes xsave avx f16c rdrand lahf_lm abm invpcid_single \
fsgsbase tsc_adjust bmi1 avx2 smep bmi2 erms invpcid xsaveopt
bogomips\t: 6584.00
"""
try:
    with open("/proc/cpuinfo", "w") as _f:
        _f.write(_CPUINFO)
except (PermissionError, OSError):
    pass

# /etc/os-release — Spack's LinuxDistro detection reads this file to determine
# the OS name and version (e.g. "ubuntu22.04").  Without it the OS field is
# empty, which causes spec-parsing failures like "expected a single spec, but
# got more: platform=linux os= target=haswell".
_OS_RELEASE = """\
NAME="Ubuntu"
VERSION="22.04.3 LTS (Jammy Jellyfish)"
ID=ubuntu
ID_LIKE=debian
VERSION_ID="22.04"
PRETTY_NAME="Ubuntu 22.04.3 LTS"
"""
try:
    os.makedirs("/etc", exist_ok=True)
    with open("/etc/os-release", "x") as _f:  # "x" = exclusive create; skips if file exists
        _f.write(_OS_RELEASE)
except (FileExistsError, PermissionError, OSError):
    pass

# ---------------------------------------------------------------------------
# 5.  Patch grp / pwd modules (may not exist in Pyodide)
# ---------------------------------------------------------------------------
try:
    import grp  # noqa: F401
except ImportError:
    import types

    _grp = types.ModuleType("grp")

    class _grpstruct:
        gr_name = "pyodide"
        gr_passwd = "x"
        gr_gid = 1000
        gr_mem = []

    _grp.getgrnam = lambda name: _grpstruct()
    _grp.getgrgid = lambda gid: _grpstruct()
    _grp.getgrall = lambda: [_grpstruct()]
    sys.modules["grp"] = _grp

try:
    import pwd  # noqa: F401
except ImportError:
    import types

    _pwd = types.ModuleType("pwd")

    class _pwdstruct:
        pw_name = "pyodide"
        pw_passwd = "x"
        pw_uid = 1000
        pw_gid = 1000
        pw_gecos = "Pyodide User"
        pw_dir = "/home/pyodide"
        pw_shell = "/bin/sh"

    _pwd.getpwnam = lambda name: _pwdstruct()
    _pwd.getpwuid = lambda uid: _pwdstruct()
    _pwd.getpwall = lambda: [_pwdstruct()]
    sys.modules["pwd"] = _pwd

# ---------------------------------------------------------------------------
# 6.  Patch termios module (removed from Pyodide — no real TTY in a browser)
# ---------------------------------------------------------------------------
# termios is used in spack/new_installer.py (unguarded) and
# spack/llnl/util/tty/log.py (already guarded with try/except).
# The shim exposes the constants and no-op functions needed for import to
# succeed; actual terminal-control calls are silently ignored.
try:
    import termios  # noqa: F401
except ImportError:
    import types

    _termios = types.ModuleType("termios")

    # tcsetattr 'when' constants
    _termios.TCSANOW = 0
    _termios.TCSADRAIN = 1
    _termios.TCSAFLUSH = 2
    _termios.TCSASOFT = 16  # BSD/macOS extension; harmless to define

    # lflag bits used by Spack (cfg[3])
    _termios.ICANON = 2
    _termios.ECHO = 8
    _termios.ECHOE = 16
    _termios.ECHOK = 32
    _termios.ECHONL = 64
    _termios.ISIG = 1
    _termios.NOFLSH = 128
    _termios.TOSTOP = 256

    # iflag bits
    _termios.BRKINT = 2
    _termios.ICRNL = 256
    _termios.IXON = 1024
    _termios.IXOFF = 4096

    # oflag bits
    _termios.OPOST = 1

    # cflag bits / speeds
    _termios.B9600 = 13
    _termios.CS8 = 48
    _termios.CREAD = 128
    _termios.CLOCAL = 2048

    # Return a dummy attribute list: [iflag, oflag, cflag, lflag, ispeed, ospeed, cc]
    # lflag = 0 means no flags are set (ICANON and ECHO are disabled because their
    # bit values are not set in lflag), which is the safe-for-browser default.
    _termios.tcgetattr = lambda fd: [0, 0, 0, 0, 0, 0, [0] * 32]
    _termios.tcsetattr = lambda fd, when, attrs: None
    _termios.tcdrain = lambda fd: None
    _termios.tcflush = lambda fd, queue: None
    _termios.tcflow = lambda fd, action: None
    _termios.tcsendbreak = lambda fd, duration: None
    _termios.tcgetpgrp = lambda fd: 1
    _termios.tcsetpgrp = lambda fd, pg: None

    sys.modules["termios"] = _termios

# ---------------------------------------------------------------------------
# 7.  Patch tty module (removed from Pyodide — wraps termios / ioctl)
# ---------------------------------------------------------------------------
# tty is used in spack/new_installer.py (unguarded top-level import).
# setcbreak / setraw are no-ops because there is no real terminal.
try:
    import tty  # noqa: F401
except ImportError:
    import types

    _tty = types.ModuleType("tty")

    _tty.IFLAG = 0
    _tty.OFLAG = 1
    _tty.CFLAG = 2
    _tty.LFLAG = 3
    _tty.ISPEED = 4
    _tty.OSPEED = 5
    _tty.CC = 6

    _tty.setraw = lambda fd, when=None: None
    _tty.setcbreak = lambda fd, when=None: None

    sys.modules["tty"] = _tty

# ---------------------------------------------------------------------------
# 8.  Patch readline module (removed from Pyodide — GNU readline not present)
# ---------------------------------------------------------------------------
# spack/cmd/python.py pushes "import readline" into a code.InteractiveConsole
# so the import happens at run-time, not module-load time.  The shim prevents
# an ImportError from bubbling up if 'spack python' is ever invoked.
try:
    import readline  # noqa: F401
except ImportError:
    import types

    _readline = types.ModuleType("readline")

    _readline.get_completer = lambda: None
    _readline.set_completer = lambda fn=None: None
    _readline.parse_and_bind = lambda s: None
    _readline.read_history_file = lambda filename=None: None
    _readline.write_history_file = lambda filename=None: None
    _readline.get_history_length = lambda: 0
    _readline.set_history_length = lambda n: None
    _readline.clear_history = lambda: None
    _readline.get_current_history_length = lambda: 0
    _readline.get_history_item = lambda idx: None
    _readline.remove_history_item = lambda pos: None
    _readline.replace_history_item = lambda pos, line: None
    _readline.redisplay = lambda: None
    _readline.set_startup_hook = lambda fn=None: None
    _readline.set_pre_input_hook = lambda fn=None: None
    _readline.set_completer_delims = lambda s: None
    _readline.get_completer_delims = lambda: " \t\n`~!@#$%^&*()-=+[{]}\\|;:'\",<>/?"

    sys.modules["readline"] = _readline

# ---------------------------------------------------------------------------
# 9.  Patch fcntl module (removed from Pyodide due to browser limitations)
# ---------------------------------------------------------------------------
try:
    import fcntl  # noqa: F401
except ImportError:
    import types

    _fcntl = types.ModuleType("fcntl")

    # Common constants used for file locking / descriptor flags
    _fcntl.LOCK_SH = 1
    _fcntl.LOCK_EX = 2
    _fcntl.LOCK_NB = 4
    _fcntl.LOCK_UN = 8
    _fcntl.F_GETFD = 1
    _fcntl.F_SETFD = 2
    _fcntl.F_GETFL = 3
    _fcntl.F_SETFL = 4
    _fcntl.FD_CLOEXEC = 1

    import errno as _errno

    # File-locking calls are no-ops — locking is meaningless in a
    # single-threaded browser sandbox where concurrent access cannot occur.
    _fcntl.flock = lambda fd, operation: None
    _fcntl.lockf = lambda fd, cmd, len=0, start=0, whence=0: None

    # fcntl() and ioctl() cannot be meaningfully implemented in a WASM
    # sandbox.  Raise an explicit OSError so callers know the operation
    # failed rather than silently continuing with bogus flag / buffer data.
    def _fcntl_stub(fd, cmd, arg=0):
        raise OSError(_errno.ENOSYS, "Function not implemented")

    def _ioctl_stub(fd, request, arg=0, mutate_flag=True):
        raise OSError(_errno.ENOTTY, "Inappropriate ioctl for device")

    _fcntl.fcntl = _fcntl_stub
    _fcntl.ioctl = _ioctl_stub

    sys.modules["fcntl"] = _fcntl


# 10.  Patch ssl module (unvendored from Pyodide stdlib)
#     Spack imports ssl transitively (via urllib / http.client).  We provide
#     enough of the public API to satisfy import-time attribute lookups while
#     keeping actual SSL connections impossible (the browser sandbox handles
#     TLS at the JS layer anyway).
# ---------------------------------------------------------------------------
try:
    import ssl  # noqa: F401
except ImportError:
    import types

    _ssl = types.ModuleType("ssl")

    # --- Exception hierarchy -------------------------------------------------
    class _SSLError(OSError):
        pass

    class _SSLEOFError(_SSLError):
        pass

    class _SSLWantReadError(_SSLError):
        pass

    class _SSLWantWriteError(_SSLError):
        pass

    class _CertificateError(ValueError):
        pass

    _ssl.SSLError = _SSLError
    _ssl.SSLEOFError = _SSLEOFError
    _ssl.SSLWantReadError = _SSLWantReadError
    _ssl.SSLWantWriteError = _SSLWantWriteError
    _ssl.CertificateError = _CertificateError

    # --- Protocol constants --------------------------------------------------
    _ssl.PROTOCOL_TLS = 2
    _ssl.PROTOCOL_TLS_CLIENT = 16
    _ssl.PROTOCOL_TLS_SERVER = 17
    _ssl.PROTOCOL_SSLv23 = 2  # legacy alias for PROTOCOL_TLS

    # --- Certificate verification constants ----------------------------------
    _ssl.CERT_NONE = 0
    _ssl.CERT_OPTIONAL = 1
    _ssl.CERT_REQUIRED = 2

    # --- Options -------------------------------------------------------------
    _ssl.OP_ALL = 0x80000054
    _ssl.OP_NO_SSLv2 = 0x01000000
    _ssl.OP_NO_SSLv3 = 0x02000000
    _ssl.OP_NO_TLSv1 = 0x04000000
    _ssl.OP_NO_TLSv1_1 = 0x10000000
    _ssl.OP_NO_TLSv1_2 = 0x20000000
    _ssl.OP_NO_COMPRESSION = 0x00020000

    # --- Purpose enum-like object used by create_default_context -------------
    class _Purpose:
        SERVER_AUTH = object()
        CLIENT_AUTH = object()

    _ssl.Purpose = _Purpose

    # --- SSLContext stub ------------------------------------------------------
    class _SSLContext:
        def __init__(self, protocol=None):
            self.check_hostname = False
            self.verify_mode = _ssl.CERT_NONE
            self.options = _ssl.OP_ALL

        def load_verify_locations(self, cafile=None, capath=None, cadata=None):
            pass

        def load_cert_chain(self, certfile, keyfile=None, password=None):
            pass

        def set_default_verify_paths(self):
            pass

        def set_ciphers(self, ciphers):
            pass

        def wrap_socket(self, sock, server_side=False, do_handshake_on_connect=True,
                        suppress_ragged_eofs=True, server_hostname=None):
            raise _SSLError("SSL wrapping is not supported in the Pyodide WebAssembly environment")

    _ssl.SSLContext = _SSLContext

    # --- Convenience helpers -------------------------------------------------
    def _create_default_context(purpose=None, *, cafile=None, capath=None, cadata=None):
        return _SSLContext(_ssl.PROTOCOL_TLS_CLIENT)

    _ssl.create_default_context = _create_default_context
    _ssl._create_default_https_context = _create_default_context
    _ssl._create_unverified_context = _create_default_context

    def _wrap_socket(sock, keyfile=None, certfile=None, server_side=False,
                     cert_reqs=None, ssl_version=None, ca_certs=None,
                     do_handshake_on_connect=True, suppress_ragged_eofs=True,
                     ciphers=None):
        raise _SSLError("SSL wrapping is not supported in the Pyodide WebAssembly environment")

    _ssl.wrap_socket = _wrap_socket

    sys.modules["ssl"] = _ssl

# ---------------------------------------------------------------------------
# 11.  Patch lzma module (unvendored from Pyodide stdlib)
#     Spack uses lzma for .tar.xz archives.  We expose the full public API so
#     that import-time code succeeds; actual compression/decompression raises
#     LZMAError because no WASM lzma implementation is available by default.
# ---------------------------------------------------------------------------
try:
    import lzma  # noqa: F401
except ImportError:
    import types

    _lzma = types.ModuleType("lzma")

    class _LZMAError(Exception):
        pass

    _lzma.LZMAError = _LZMAError

    # Format constants
    _lzma.FORMAT_AUTO = 0
    _lzma.FORMAT_XZ = 1
    _lzma.FORMAT_ALONE = 2
    _lzma.FORMAT_RAW = 3

    # Check constants
    _lzma.CHECK_NONE = 0
    _lzma.CHECK_CRC32 = 1
    _lzma.CHECK_CRC64 = 4
    _lzma.CHECK_SHA256 = 10
    _lzma.CHECK_ID_MAX = 15
    _lzma.CHECK_UNKNOWN = 16

    # Filter IDs
    _lzma.FILTER_LZMA1 = 0x09300e5a
    _lzma.FILTER_LZMA2 = 0x21
    _lzma.FILTER_DELTA = 0x03
    _lzma.FILTER_X86 = 0x04
    _lzma.FILTER_IA64 = 0x06
    _lzma.FILTER_ARM = 0x07
    _lzma.FILTER_ARMTHUMB = 0x08
    _lzma.FILTER_SPARC = 0x09
    _lzma.FILTER_POWERPC = 0x05

    # Preset / mode constants
    _lzma.PRESET_DEFAULT = 6
    _lzma.PRESET_EXTREME = (1 << 31)
    _lzma.MODE_FAST = 1
    _lzma.MODE_NORMAL = 2
    _lzma.MF_HC3 = 3
    _lzma.MF_HC4 = 4
    _lzma.MF_BT2 = 18
    _lzma.MF_BT3 = 19
    _lzma.MF_BT4 = 20

    _LZMA_UNAVAILABLE = "lzma compression is not available in the Pyodide WebAssembly environment"

    def compress(data, format=_lzma.FORMAT_XZ, check=-1, preset=None, filters=None):
        raise _LZMAError(_LZMA_UNAVAILABLE)

    def decompress(data, format=_lzma.FORMAT_AUTO, memlimit=None, filters=None):
        raise _LZMAError(_LZMA_UNAVAILABLE)

    _lzma.compress = compress
    _lzma.decompress = decompress

    class _LZMAFile:
        def __init__(self, *args, **kwargs):
            raise _LZMAError(_LZMA_UNAVAILABLE)

    class _LZMACompressor:
        def __init__(self, *args, **kwargs):
            raise _LZMAError(_LZMA_UNAVAILABLE)

    class _LZMADecompressor:
        def __init__(self, *args, **kwargs):
            raise _LZMAError(_LZMA_UNAVAILABLE)

    _lzma.LZMAFile = _LZMAFile
    _lzma.LZMACompressor = _LZMACompressor
    _lzma.LZMADecompressor = _LZMADecompressor

    def open(*args, **kwargs):  # noqa: A001 — shadows builtin at module scope; use builtins.open elsewhere
        raise _LZMAError(_LZMA_UNAVAILABLE)

    _lzma.open = open

    sys.modules["lzma"] = _lzma

# ---------------------------------------------------------------------------
# 12.  Fake executable stubs in the MEMFS PATH
#
#      Spack's which_string() checks whether executables exist on the
#      filesystem (is_file() + os.access(..., os.X_OK)) before attempting to
#      run them.  In the Pyodide WASM environment there is no real /usr/bin,
#      so which_string() always returns None and calls like
#      spack.util.git.git(required=True) — triggered by `spack info` when it
#      inspects a package that has a git-based version — raise:
#
#          Error: spack requires 'git'. Make sure it is in your path.
#
#      Creating lightweight placeholder files with mode 0o755 satisfies the
#      filesystem check.  Actual execution is intercepted by the subprocess
#      shim (section 2) which returns canned mock responses, so the stub
#      script content is never read.
# ---------------------------------------------------------------------------
_STUB_SCRIPT = b"#!/bin/sh\n# browser stub -- intercepted by subprocess shim\n"

_FAKE_EXECUTABLES = [
    "/usr/bin/git",
    "/usr/bin/gcc",
    "/usr/bin/g++",
    "/usr/bin/gfortran",
    "/usr/bin/make",
    "/usr/bin/cmake",
    "/usr/bin/clingo",
    "/usr/bin/patch",
    "/usr/bin/tar",
    "/usr/bin/curl",
    "/usr/bin/unzip",
]

for _exe_path in _FAKE_EXECUTABLES:
    try:
        _exe_dir = os.path.dirname(_exe_path)
        os.makedirs(_exe_dir, exist_ok=True)
        if not os.path.exists(_exe_path):
            with builtins.open(_exe_path, "wb") as _f:
                _f.write(_STUB_SCRIPT)
            os.chmod(_exe_path, 0o755)
    except (PermissionError, OSError):
        pass  # Silently skip on non-Pyodide hosts

# ---------------------------------------------------------------------------
# 13.  Patch _multiprocessing (C extension removed from Pyodide stdlib)
#
#      Pyodide removes the _multiprocessing C extension because fork(2) and
#      POSIX semaphores are unavailable in the browser/WASM environment.
#      This causes ``import multiprocessing`` to fail as soon as any code
#      path touches multiprocessing.synchronize (which imports _multiprocessing
#      for SemLock).  Spack's concretizer / spec machinery can trigger this
#      import path.
#
#      Strategy: register a pure-Python stub for ``_multiprocessing`` before
#      the real multiprocessing package is first imported.  The stub backs
#      SemLock with threading.Lock / threading.Semaphore so that primitives
#      function correctly in the single-threaded async WASM runtime.
#      ``multiprocessing.cpu_count()`` already delegates to ``os.cpu_count()``
#      on CPython 3.8+ so no extra patching is needed there.
# ---------------------------------------------------------------------------
try:
    import _multiprocessing  # noqa: F401 — already present (CPython); nothing to do
except ImportError:
    import threading
    import types

    _mp_stub = types.ModuleType("_multiprocessing")

    # flags dict expected by multiprocessing.synchronize
    _mp_stub.flags = {"HAVE_SEM_OPEN": 0, "HAVE_SEM_TIMEDWAIT": 0}

    # sem_unlink is a no-op stub (named semaphores don't exist in WASM)
    def _sem_unlink(name):
        pass

    _mp_stub.sem_unlink = _sem_unlink

    # SemLock kinds (mirror CPython constants from CPython's
    # Lib/multiprocessing/synchronize.py: RECURSIVE_MUTEX, SEMAPHORE = list(range(2)))
    _SEM_LOCK_RECURSIVE_MUTEX = 0
    _SEM_LOCK_SEMAPHORE = 1

    class SemLock:
        """Pure-Python SemLock backed by threading primitives.

        Supports context-manager protocol (acquire/release/__enter__/__exit__)
        so that multiprocessing.Lock, multiprocessing.Semaphore, etc. work
        without the C extension.
        """

        SEM_VALUE_MAX = (1 << 31) - 1

        def __init__(self, kind, value, maxvalue, name="", unlink=True):
            self.kind = kind
            self.maxvalue = maxvalue
            self.name = name
            self._value = value  # track current semaphore value
            if kind == _SEM_LOCK_RECURSIVE_MUTEX:
                self._lock = threading.RLock()
            else:
                self._lock = threading.Semaphore(value)
            self.handle = id(self._lock)

        def acquire(self, block=True, timeout=None):
            if not block:
                result = self._lock.acquire(blocking=False)
            elif timeout is not None:
                result = self._lock.acquire(blocking=True, timeout=timeout)
            else:
                result = self._lock.acquire(blocking=True)
            if result and self.kind != _SEM_LOCK_RECURSIVE_MUTEX:
                self._value = max(0, self._value - 1)
            return result

        def release(self):
            self._lock.release()
            if self.kind != _SEM_LOCK_RECURSIVE_MUTEX:
                self._value = min(self.maxvalue, self._value + 1)

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *args):
            self.release()

        def _count(self):
            # Stub: tracking recursive lock depth is not needed in the
            # single-threaded Pyodide environment.
            return 1

        def _is_mine(self):
            # Stub: no cross-thread ownership tracking needed in WASM.
            return True

        def _is_zero(self):
            return self._value == 0

        def _get_value(self):
            return self._value

        def _after_fork(self):
            pass

        @staticmethod
        def _rebuild(handle, kind, maxvalue, name):
            # _rebuild is called when unpickling a SemLock across processes.
            # In the Pyodide environment there are no real OS semaphores to
            # reopen via ``handle``, so we create a fresh threading primitive.
            # This is acceptable because spack does not pickle synchronization
            # objects in the browser context.
            obj = object.__new__(SemLock)
            obj.handle = handle
            obj.kind = kind
            obj.maxvalue = maxvalue
            obj.name = name
            obj._value = 1
            if kind == _SEM_LOCK_RECURSIVE_MUTEX:
                obj._lock = threading.RLock()
            else:
                obj._lock = threading.Semaphore(1)
            return obj

    _mp_stub.SemLock = SemLock

    sys.modules["_multiprocessing"] = _mp_stub

    # Pre-import the high-level multiprocessing package so it initialises
    # cleanly against the stub now registered in sys.modules.
    if "multiprocessing" not in sys.modules:
        try:
            import multiprocessing  # noqa: F401
        except Exception:
            pass  # Will be resolved when user code actually imports it
