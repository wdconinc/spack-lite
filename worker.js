/**
 * worker.js — Pyodide Web Worker for Spack-Lite
 *
 * Responsibilities:
 *  1. Load Pyodide (WASM Python runtime)
 *  2. Fetch and unpack spack-lite.tar.gz into /home/pyodide/spack (MEMFS)
 *  3. Execute shim_system.py to monkey-patch subprocess / os / platform
 *  4. Inject a fake compiler + package configuration into ~/.spack
 *  5. Receive { type: 'run', command: '...' } messages and return results
 */

'use strict';

// ---------------------------------------------------------------------------
// Pyodide CDN URL — pin to a specific version for reproducibility
// ---------------------------------------------------------------------------
const PYODIDE_CDN = 'https://cdn.jsdelivr.net/pyodide/v0.25.1/full/pyodide.js';

// URL of the stripped-down Spack tarball (relative to the page origin).
// Build it with  scripts/make_spack_lite.sh  and serve it alongside index.html.
const SPACK_LITE_URL = 'spack-lite.tar.gz';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function post(type, payload) {
  self.postMessage({ type, ...payload });
}

function setStatus(state, message) {
  post('status', { state, message });
}

// Redirect Python stdout/stderr so we can stream it to the terminal.
const STDOUT_REDIRECT = `
import sys, js

class _JsWriter:
    def write(self, text):
        if text:
            js.postMessage(js.Object.fromEntries([['type','stdout'],['text', text]]))
    def flush(self):
        pass

sys.stdout = _JsWriter()
sys.stderr = _JsWriter()
`;

// ---------------------------------------------------------------------------
// Spack configuration injected into ~/.spack
// ---------------------------------------------------------------------------
const COMPILERS_YAML = `\
compilers:
- compiler:
    spec: gcc@11.4.0
    paths:
      cc: /usr/bin/gcc
      cxx: /usr/bin/g++
      f77: /usr/bin/gfortran
      fc: /usr/bin/gfortran
    flags: {}
    operating_system: ubuntu22.04
    target: x86_64
    modules: []
    environment: {}
    extra_rpaths: []
`;

const PACKAGES_YAML = `\
packages:
  all:
    target: [x86_64]
    providers:
      mpi: [openmpi]
      blas: [openblas]
      lapack: [openblas]
`;

const CONFIG_YAML = `\
config:
  concretizer: clingo
  checksum: false
  verify_ssl: false
  install_missing_compilers: false
  build_jobs: 1
  db_lock_timeout: 60
`;

// ---------------------------------------------------------------------------
// Main initialisation
// ---------------------------------------------------------------------------
let pyodide = null;
let spackLoaded = false;

async function init() {
  try {
    // 1. Load Pyodide
    setStatus('loading', 'Loading Pyodide…');
    importScripts(PYODIDE_CDN);
    pyodide = await loadPyodide();

    // 2. Redirect stdout/stderr to the terminal
    await pyodide.runPythonAsync(STDOUT_REDIRECT);

    // 3. Fetch and unpack spack-lite.tar.gz
    setStatus('loading', 'Fetching spack-lite archive…');
    try {
      const response = await fetch(SPACK_LITE_URL);
      if (response.ok) {
        const buffer = await response.arrayBuffer();
        setStatus('loading', 'Unpacking spack-lite…');
        await pyodide.runPythonAsync(`
import os
os.makedirs('/home/pyodide/spack', exist_ok=True)
`);
        pyodide.unpackArchive(buffer, 'gztar', { extractDir: '/home/pyodide' });
        spackLoaded = true;
      }
    } catch (fetchErr) {
      console.warn('spack-lite.tar.gz not found — running in demo mode:', fetchErr);
    }

    // 4. Add Spack to sys.path (if unpacked) and set up the environment
    setStatus('loading', 'Configuring environment…');
    // When spack-lite.tar.gz was not available, Spack commands will produce
    // a clear "module not found" error in the terminal (demo mode).
    await pyodide.runPythonAsync(`
import sys, os

if os.path.isdir('/home/pyodide/spack/lib/spack'):
    spack_lib = '/home/pyodide/spack/lib/spack'
    spack_external = '/home/pyodide/spack/lib/spack/external'
    for p in [spack_lib, spack_external]:
        if p not in sys.path:
            sys.path.insert(0, p)

# Set SPACK_ROOT so Spack can locate its own config files
os.environ['SPACK_ROOT'] = '/home/pyodide/spack'
os.environ['HOME'] = '/home/pyodide'
`);

    // 5. Create ~/.spack configuration directories
    await pyodide.runPythonAsync(`
import os

spack_cfg_linux = '/home/pyodide/.spack/linux'
os.makedirs(spack_cfg_linux, exist_ok=True)

cfg_files = {
    '/home/pyodide/.spack/config.yaml':        ${JSON.stringify(CONFIG_YAML)},
    '/home/pyodide/.spack/linux/compilers.yaml': ${JSON.stringify(COMPILERS_YAML)},
    '/home/pyodide/.spack/packages.yaml':       ${JSON.stringify(PACKAGES_YAML)},
}
for path, content in cfg_files.items():
    with open(path, 'w') as f:
        f.write(content)
`);

    // 6. Load and execute the system shim
    setStatus('loading', 'Applying system shims…');
    const shimResponse = await fetch('shim_system.py');
    let shimCode;
    if (shimResponse.ok) {
      shimCode = await shimResponse.text();
    } else {
      // Inline fallback shim in case we can't fetch the file
      shimCode = INLINE_SHIM;
    }
    await pyodide.runPythonAsync(shimCode);

    // 7. Done
    setStatus('ready', 'Ready');

  } catch (err) {
    console.error('Pyodide init failed:', err);
    setStatus('error', 'Init failed: ' + err.message);
  }
}

// ---------------------------------------------------------------------------
// Inline fallback shim (mirrors shim_system.py — used only when the fetch
// of shim_system.py fails; keep version strings here in sync with that file)
// ---------------------------------------------------------------------------
// Shared version strings used in mock subprocess responses
const _GCC_VERSION_STR = 'gcc (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0';
const _GPP_VERSION_STR = 'g++ (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0';
const _GFC_VERSION_STR = 'GNU Fortran (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0';

const INLINE_SHIM = `
import sys
import os
import platform

# --- platform overrides ---
sys.platform = 'linux'
os.name = 'posix'

# Override platform functions so Spack detects a Linux x86_64 host
_orig_uname = os.uname if hasattr(os, 'uname') else None

class _FakeUname:
    sysname  = 'Linux'
    nodename = 'spack-browser'
    release  = '5.15.0'
    version  = '#1 SMP'
    machine  = 'x86_64'
    def __iter__(self):
        return iter([self.sysname, self.nodename, self.release, self.version, self.machine])

os.uname = lambda: _FakeUname()
platform.machine    = lambda: 'x86_64'
platform.system     = lambda: 'Linux'
platform.release    = lambda: '5.15.0'
platform.node       = lambda: 'spack-browser'
platform.processor  = lambda: 'x86_64'

def _fake_uname_result():
    import collections
    fields = ['system','node','release','version','machine','processor']
    T = collections.namedtuple('uname_result', fields)
    return T('Linux','spack-browser','5.15.0','#1 SMP','x86_64','x86_64')
platform.uname = _fake_uname_result

# --- subprocess shim ---
import subprocess
from unittest.mock import MagicMock

def _mock_run(args=None, *extra_args, **kwargs):
    cmd = ' '.join(map(str, args)) if isinstance(args, (list, tuple)) else str(args or '')
    stdout = b''
    stderr = b''
    returncode = 0

    if 'uname' in cmd:
        stdout = b'x86_64'
    elif 'gcc' in cmd or 'cc' in cmd:
        stdout = b'${_GCC_VERSION_STR}\\n'
    elif 'g++' in cmd or 'c++' in cmd:
        stdout = b'${_GPP_VERSION_STR}\\n'
    elif 'gfortran' in cmd:
        stdout = b'${_GFC_VERSION_STR}\\n'
    elif 'git' in cmd:
        stdout = b'git version 2.34.1\\n'
    elif 'lscpu' in cmd:
        stdout = b'Architecture: x86_64\\nCPU(s): 4\\n'
    elif 'clingo' in cmd:
        stdout = b'clingo version 5.6.2\\n'

    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    result.args = args
    return result

class _MockPopen:
    def __init__(self, args=None, *extra, **kwargs):
        self._result = _mock_run(args)
    def communicate(self, input=None, timeout=None):
        return self._result.stdout, self._result.stderr
    def wait(self, timeout=None):
        return 0
    def __enter__(self): return self
    def __exit__(self, *a): pass
    stdout = None
    stderr = None
    returncode = 0
    pid = 12345

subprocess.run   = _mock_run
subprocess.call  = lambda *a, **kw: 0
subprocess.check_call = lambda *a, **kw: 0
subprocess.check_output = lambda args=None, *extra, **kw: _mock_run(args).stdout
subprocess.Popen = _MockPopen

# Prevent Spack from trying to write to real filesystem paths
os.makedirs('/tmp/spack-stage', exist_ok=True)
os.environ.setdefault('SPACK_DISABLE_LOCAL_CONFIG', '0')
os.environ.setdefault('SPACK_USER_CONFIG_PATH', '/home/pyodide/.spack')

# --- fcntl shim (removed from Pyodide) ---
try:
    import fcntl
except ImportError:
    import types as _types
    _fcntl = _types.ModuleType('fcntl')
    _fcntl.LOCK_SH = 1
    _fcntl.LOCK_EX = 2
    _fcntl.LOCK_NB = 4
    _fcntl.LOCK_UN = 8
    _fcntl.F_GETFD = 1
    _fcntl.F_SETFD = 2
    _fcntl.F_GETFL = 3
    _fcntl.F_SETFL = 4
    _fcntl.FD_CLOEXEC = 1
    _fcntl.flock = lambda fd, op: None
    _fcntl.lockf = lambda fd, cmd, len=0, start=0, whence=0: None
    import errno as _errno
    def _fcntl_stub(fd, cmd, arg=0):
        raise OSError(_errno.ENOSYS, 'Function not implemented')
    def _ioctl_stub(fd, req, arg=0, mutate_flag=True):
        raise OSError(_errno.ENOTTY, 'Inappropriate ioctl for device')
    _fcntl.fcntl = _fcntl_stub
    _fcntl.ioctl = _ioctl_stub
    sys.modules['fcntl'] = _fcntl

# --- termios shim (removed from Pyodide) ---
try:
    import termios
except ImportError:
    import types as _types
    _termios = _types.ModuleType('termios')
    _termios.IGNBRK  = 0o000001
    _termios.BRKINT  = 0o000002
    _termios.IGNPAR  = 0o000004
    _termios.INPCK   = 0o000020
    _termios.ISTRIP  = 0o000040
    _termios.INLCR   = 0o000100
    _termios.IGNCR   = 0o000200
    _termios.ICRNL   = 0o000400
    _termios.IXON    = 0o002000
    _termios.OPOST   = 0o000001
    _termios.CS8     = 0o000060
    _termios.CREAD   = 0o000200
    _termios.CLOCAL  = 0o004000
    _termios.ISIG    = 0o000001
    _termios.ICANON  = 0o000002
    _termios.ECHO    = 0o000010
    _termios.ECHOE   = 0o000020
    _termios.ECHOK   = 0o000040
    _termios.ECHONL  = 0o000100
    _termios.NOFLSH  = 0o000200
    _termios.IEXTEN  = 0o100000
    _termios.TCSANOW   = 0
    _termios.TCSADRAIN = 1
    _termios.TCSAFLUSH = 2
    _termios.B0      = 0o000000
    _termios.B9600   = 0o000015
    _termios.B19200  = 0o000016
    _termios.B38400  = 0o000017
    _termios.B57600  = 0o010001
    _termios.B115200 = 0o010002
    _termios.VMIN    = 6
    _termios.VTIME   = 5
    _termios.NCCS    = 32
    _termios.error   = OSError
    def _tcgetattr(fd):
        cc = [b'\\x00'] * _termios.NCCS
        cc[_termios.VMIN]  = b'\\x01'
        cc[_termios.VTIME] = b'\\x00'
        return [_termios.ICRNL, _termios.OPOST,
                _termios.CS8 | _termios.CREAD | _termios.CLOCAL,
                _termios.ECHO | _termios.ICANON | _termios.ISIG | _termios.IEXTEN,
                _termios.B9600, _termios.B9600, cc]
    _termios.tcgetattr  = _tcgetattr
    _termios.tcsetattr  = lambda fd, when, attrs: None
    _termios.tcsendbreak = lambda fd, duration: None
    _termios.tcdrain    = lambda fd: None
    _termios.tcflush    = lambda fd, queue: None
    _termios.tcflow     = lambda fd, action: None
    sys.modules['termios'] = _termios

# --- ssl shim (unvendored from Pyodide stdlib) ---
try:
    import ssl
except ImportError:
    import types as _types
    _ssl = _types.ModuleType('ssl')
    class _SSLError(OSError): pass
    class _SSLEOFError(_SSLError): pass
    class _SSLWantReadError(_SSLError): pass
    class _SSLWantWriteError(_SSLError): pass
    class _CertificateError(ValueError): pass
    _ssl.SSLError = _SSLError
    _ssl.SSLEOFError = _SSLEOFError
    _ssl.SSLWantReadError = _SSLWantReadError
    _ssl.SSLWantWriteError = _SSLWantWriteError
    _ssl.CertificateError = _CertificateError
    _ssl.PROTOCOL_TLS = 2
    _ssl.PROTOCOL_TLS_CLIENT = 16
    _ssl.PROTOCOL_TLS_SERVER = 17
    _ssl.PROTOCOL_SSLv23 = 2
    _ssl.CERT_NONE = 0
    _ssl.CERT_OPTIONAL = 1
    _ssl.CERT_REQUIRED = 2
    _ssl.OP_ALL = 0x80000054
    _ssl.OP_NO_SSLv2 = 0x01000000
    _ssl.OP_NO_SSLv3 = 0x02000000
    _ssl.OP_NO_TLSv1 = 0x04000000
    _ssl.OP_NO_TLSv1_1 = 0x10000000
    _ssl.OP_NO_TLSv1_2 = 0x20000000
    _ssl.OP_NO_COMPRESSION = 0x00020000
    class _Purpose:
        SERVER_AUTH = object()
        CLIENT_AUTH = object()
    _ssl.Purpose = _Purpose
    class _SSLContext:
        def __init__(self, protocol=None):
            self.check_hostname = False
            self.verify_mode = _ssl.CERT_NONE
            self.options = _ssl.OP_ALL
        def load_verify_locations(self, cafile=None, capath=None, cadata=None): pass
        def load_cert_chain(self, certfile, keyfile=None, password=None): pass
        def set_default_verify_paths(self): pass
        def set_ciphers(self, ciphers): pass
        def wrap_socket(self, sock, server_side=False, do_handshake_on_connect=True,
                        suppress_ragged_eofs=True, server_hostname=None):
            raise _SSLError('SSL wrapping is not supported in the Pyodide WebAssembly environment')
    _ssl.SSLContext = _SSLContext
    def _create_default_context(purpose=None, *, cafile=None, capath=None, cadata=None):
        return _SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
    _ssl.create_default_context = _create_default_context
    _ssl._create_default_https_context = _create_default_context
    _ssl._create_unverified_context = _create_default_context
    def _wrap_socket(sock, keyfile=None, certfile=None, server_side=False,
                     cert_reqs=None, ssl_version=None, ca_certs=None,
                     do_handshake_on_connect=True, suppress_ragged_eofs=True, ciphers=None):
        raise _SSLError('SSL wrapping is not supported in the Pyodide WebAssembly environment')
    _ssl.wrap_socket = _wrap_socket
    sys.modules['ssl'] = _ssl

# --- lzma shim (unvendored from Pyodide stdlib) ---
try:
    import lzma
except ImportError:
    import types as _types
    _lzma = _types.ModuleType('lzma')
    class _LZMAError(Exception): pass
    _lzma.LZMAError = _LZMAError
    _lzma.FORMAT_AUTO = 0
    _lzma.FORMAT_XZ = 1
    _lzma.FORMAT_ALONE = 2
    _lzma.FORMAT_RAW = 3
    _lzma.CHECK_NONE = 0
    _lzma.CHECK_CRC32 = 1
    _lzma.CHECK_CRC64 = 4
    _lzma.CHECK_SHA256 = 10
    _lzma.CHECK_ID_MAX = 15
    _lzma.CHECK_UNKNOWN = 16
    _lzma.FILTER_LZMA1 = 0x09300e5a
    _lzma.FILTER_LZMA2 = 0x21
    _lzma.FILTER_DELTA = 0x03
    _lzma.FILTER_X86 = 0x04
    _lzma.FILTER_IA64 = 0x06
    _lzma.FILTER_ARM = 0x07
    _lzma.FILTER_ARMTHUMB = 0x08
    _lzma.FILTER_SPARC = 0x09
    _lzma.FILTER_POWERPC = 0x05
    _lzma.PRESET_DEFAULT = 6
    _lzma.PRESET_EXTREME = (1 << 31)
    _lzma.MODE_FAST = 1
    _lzma.MODE_NORMAL = 2
    _lzma.MF_HC3 = 3
    _lzma.MF_HC4 = 4
    _lzma.MF_BT2 = 18
    _lzma.MF_BT3 = 19
    _lzma.MF_BT4 = 20
    _LZMA_MSG = 'lzma compression is not available in the Pyodide WebAssembly environment'
    def _lzma_compress(data, format=_lzma.FORMAT_XZ, check=-1, preset=None, filters=None):
        raise _LZMAError(_LZMA_MSG)
    def _lzma_decompress(data, format=_lzma.FORMAT_AUTO, memlimit=None, filters=None):
        raise _LZMAError(_LZMA_MSG)
    _lzma.compress = _lzma_compress
    _lzma.decompress = _lzma_decompress
    class _LZMAFile:
        def __init__(self, *a, **kw): raise _LZMAError(_LZMA_MSG)
    class _LZMACompressor:
        def __init__(self, *a, **kw): raise _LZMAError(_LZMA_MSG)
    class _LZMADecompressor:
        def __init__(self, *a, **kw): raise _LZMAError(_LZMA_MSG)
    _lzma.LZMAFile = _LZMAFile
    _lzma.LZMACompressor = _LZMACompressor
    _lzma.LZMADecompressor = _LZMADecompressor
    def _lzma_open(*a, **kw): raise _LZMAError(_LZMA_MSG)
    _lzma.open = _lzma_open
    sys.modules['lzma'] = _lzma
`;

// ---------------------------------------------------------------------------
// Command runner
// ---------------------------------------------------------------------------
const RUN_COMMAND_PY = `
import io, sys

def _run_spack_command(command_str):
    """Execute a spack CLI command and return captured output as a string."""
    parts = command_str.strip().split()
    if not parts:
        return ''

    # Verify we are talking to spack
    if parts[0].lower() != 'spack':
        return f"Unknown command: {parts[0]}\\nTry: spack list | spack info <pkg> | spack spec <spec>\\n"

    spack_args = parts[1:]

    # Capture stdout
    buf = io.StringIO()
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    try:
        sys.stdout = buf
        sys.stderr = buf
        try:
            from spack.main import SpackCommand, SpackCommandError
            if not spack_args:
                sys.stdout = old_stdout
                sys.stderr = old_stderr
                return "Usage: spack <command> [options]\\nTry 'help' for available commands.\\n"
            cmd = SpackCommand(spack_args[0])
            cmd(*spack_args[1:])
        except SystemExit:
            pass
        except Exception as e:
            buf.write(f"\\nError: {e}\\n")
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return buf.getvalue()
`;

async function runSpackCommand(cmdStr) {
  // Make sure the helper is defined
  await pyodide.runPythonAsync(RUN_COMMAND_PY);
  const result = await pyodide.runPythonAsync(
    `_run_spack_command(${JSON.stringify(cmdStr)})`
  );
  return result ?? '';
}

// ---------------------------------------------------------------------------
// Message handler
// ---------------------------------------------------------------------------
self.onmessage = async ({ data }) => {
  if (data.type !== 'run') return;

  if (!pyodide) {
    post('error', { message: 'Pyodide is not ready yet.' });
    return;
  }

  if (!spackLoaded) {
    post('result', { output: 'Spack archive not loaded (demo mode). ' +
      'Build spack-lite.tar.gz with scripts/make_spack_lite.sh and serve it alongside index.html.\n' });
    return;
  }

  try {
    const output = await runSpackCommand(data.command);
    post('result', { output });
  } catch (err) {
    post('error', { message: String(err) });
  }
};

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
init();
