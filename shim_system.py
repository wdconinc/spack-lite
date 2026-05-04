"""
shim_system.py — The "System Lie"

This module monkey-patches the Python standard library so that Spack
believes it is running on a standard Linux/x86_64 host even though the
actual runtime is a Pyodide/Emscripten WebAssembly environment inside a
browser where fork(2), exec(2), and most POSIX syscalls are unavailable.

Load this module *before* importing any Spack code:

    exec(open('shim_system.py').read())

or, in a Pyodide worker:

    pyodide.runPythonAsync(open('shim_system.py').read())
"""

import sys
import os
import platform
import collections

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
# ---------------------------------------------------------------------------
import subprocess  # noqa: E402 — must come after platform patching
from unittest.mock import MagicMock  # noqa: E402


def _build_mock_result(args=None, **kwargs):
    """Return a Mock CompletedProcess-like object with sensible stdout."""
    cmd = " ".join(map(str, args)) if isinstance(args, (list, tuple)) else str(args or "")

    stdout = b""
    stderr = b""

    if "uname" in cmd:
        stdout = b"x86_64\n"
    elif "gcc" in cmd or ("cc" in cmd and "clang" not in cmd):
        stdout = b"gcc (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0\n"
    elif "g++" in cmd or "c++" in cmd:
        stdout = b"g++ (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0\n"
    elif "gfortran" in cmd or "fortran" in cmd:
        stdout = b"GNU Fortran (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0\n"
    elif "git" in cmd:
        if "rev-parse" in cmd or "log" in cmd:
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
    result.returncode = 0
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
]
for _d in _REQUIRED_DIRS:
    try:
        os.makedirs(_d, exist_ok=True)
    except (PermissionError, OSError):
        pass  # Silently skip on non-Pyodide hosts

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
# 6.  Patch fcntl module (removed from Pyodide due to browser limitations)
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

# ---------------------------------------------------------------------------
# 7.  Patch termios module (removed from Pyodide due to browser limitations)
# ---------------------------------------------------------------------------
try:
    import termios  # noqa: F401
except ImportError:
    import types

    _termios = types.ModuleType("termios")

    # Common input-mode flags
    _termios.IGNBRK = 0o000001
    _termios.BRKINT = 0o000002
    _termios.IGNPAR = 0o000004
    _termios.INPCK  = 0o000020
    _termios.ISTRIP = 0o000040
    _termios.INLCR  = 0o000100
    _termios.IGNCR  = 0o000200
    _termios.ICRNL  = 0o000400
    _termios.IXON   = 0o002000

    # Common output-mode flags
    _termios.OPOST  = 0o000001

    # Common control-mode flags
    _termios.CS8    = 0o000060
    _termios.CREAD  = 0o000200
    _termios.CLOCAL = 0o004000

    # Common local-mode flags
    _termios.ISIG   = 0o000001
    _termios.ICANON = 0o000002
    _termios.ECHO   = 0o000010
    _termios.ECHOE  = 0o000020
    _termios.ECHOK  = 0o000040
    _termios.ECHONL = 0o000100
    _termios.NOFLSH = 0o000200
    _termios.IEXTEN = 0o100000

    # tcsetattr 'when' values
    _termios.TCSANOW   = 0
    _termios.TCSADRAIN = 1
    _termios.TCSAFLUSH = 2

    # Baud rate constants (B0–B115200 subset)
    _termios.B0      = 0o000000
    _termios.B9600   = 0o000015
    _termios.B19200  = 0o000016
    _termios.B38400  = 0o000017
    _termios.B57600  = 0o010001
    _termios.B115200 = 0o010002

    # Special character index constants
    _termios.VMIN  = 6
    _termios.VTIME = 5

    # Number of control characters in the cc array (matches Linux/glibc NCCS)
    _termios.NCCS = 32

    # Exception type — mirrors termios.error in the real module
    _termios.error = OSError

    # tcgetattr returns a list: [iflag, oflag, cflag, lflag, ispeed, ospeed, cc]
    # Return a reasonable "dumb terminal" attribute list.
    def _tcgetattr(fd):
        cc = [b'\x00'] * _termios.NCCS
        cc[_termios.VMIN]  = b'\x01'
        cc[_termios.VTIME] = b'\x00'
        return [
            _termios.ICRNL,          # iflag
            _termios.OPOST,          # oflag
            _termios.CS8 | _termios.CREAD | _termios.CLOCAL,  # cflag
            _termios.ECHO | _termios.ICANON | _termios.ISIG | _termios.IEXTEN,  # lflag
            _termios.B9600,          # ispeed
            _termios.B9600,          # ospeed
            cc,                      # cc
        ]

    # tcsetattr and tcflush are no-ops — there is no real tty in the browser.
    _termios.tcgetattr = _tcgetattr
    _termios.tcsetattr = lambda fd, when, attrs: None
    _termios.tcsendbreak = lambda fd, duration: None
    _termios.tcdrain = lambda fd: None
    _termios.tcflush = lambda fd, queue: None
    _termios.tcflow = lambda fd, action: None

    sys.modules["termios"] = _termios

# ---------------------------------------------------------------------------
# 8.  Patch ssl module (unvendored from Pyodide stdlib)
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
            return sock

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
        return sock

    _ssl.wrap_socket = _wrap_socket

    sys.modules["ssl"] = _ssl

# ---------------------------------------------------------------------------
# 9.  Patch lzma module (unvendored from Pyodide stdlib)
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

    def _compress(data, format=_lzma.FORMAT_XZ, check=-1, preset=None, filters=None):
        raise _LZMAError(_LZMA_UNAVAILABLE)

    def _decompress(data, format=_lzma.FORMAT_AUTO, memlimit=None, filters=None):
        raise _LZMAError(_LZMA_UNAVAILABLE)

    _lzma.compress = _compress
    _lzma.decompress = _decompress

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

    def _lzma_open(*args, **kwargs):
        raise _LZMAError(_LZMA_UNAVAILABLE)

    _lzma.open = _lzma_open

    sys.modules["lzma"] = _lzma
