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
`;

// ---------------------------------------------------------------------------
// Main initialisation
// ---------------------------------------------------------------------------
let pyodide = null;

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
    let spackLoaded = false;
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
    cmd = ' '.join(args) if isinstance(args, (list, tuple)) else str(args or '')
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
