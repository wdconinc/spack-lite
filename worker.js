/**
 * worker.js — Pyodide Web Worker for Spack-Lite
 *
 * Responsibilities:
 *  1. Load Pyodide (WASM Python runtime)
 *  2. Fetch and unpack spack-lite.tar.gz into /home/pyodide/spack (MEMFS)
 *  3. Execute shim_system.py to monkey-patch os/platform/subprocess and
 *     install stubs for Pyodide-removed modules (termios, tty, readline,
 *     fcntl, grp, pwd)
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
import sys, js, io

class _JsWriter:
    def write(self, text):
        if text:
            js.postMessage(js.Object.fromEntries([['type','stdout'],['text', text]]))
    def flush(self):
        pass
    def isatty(self):
        return False
    def fileno(self):
        raise io.UnsupportedOperation('fileno')

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
    // shim_system.py is the single canonical source for all module shims.
    // It is served from the same origin as worker.js; if it cannot be
    // fetched the worker raises an error rather than continuing with a
    // partial / out-of-date fallback.
    setStatus('loading', 'Applying system shims…');
    const shimResponse = await fetch('shim_system.py');
    if (!shimResponse.ok) {
      throw new Error(`Failed to fetch shim_system.py (HTTP ${shimResponse.status})`);
    }
    const shimCode = await shimResponse.text();
    await pyodide.runPythonAsync(shimCode);

    // 7. Done
    setStatus('ready', 'Ready');

  } catch (err) {
    console.error('Pyodide init failed:', err);
    setStatus('error', 'Init failed: ' + err.message);
  }
}

// ---------------------------------------------------------------------------
// Command runner
// ---------------------------------------------------------------------------
const RUN_COMMAND_PY = `
import io, sys

class _SpackBuffer(io.StringIO):
    """StringIO that provides fileno()/isatty() so Spack's TTY-detection code
    does not raise io.UnsupportedOperation and abort the command."""
    def fileno(self):
        return 1  # pretend to be stdout; os.isatty(1) is False in Pyodide
    def isatty(self):
        return False

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
    buf = _SpackBuffer()
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
