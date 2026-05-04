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
 *  5. Execute shell.py to install the Python-backed POSIX shell interpreter
 *  6. Receive { type: 'run', command: '...' } messages and return results
 *     including the new working directory after each command
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

    // 7. Load and execute the shell interpreter
    // shell.py defines run_shell_command() which handles built-in POSIX-like
    // commands (ls, cd, cat, grep, …) and routes `spack` through the Spack
    // Python API.  It must be loaded after shim_system.py so that the module
    // shims are in place before any spack imports are attempted.
    setStatus('loading', 'Loading shell…');
    const shellResponse = await fetch('shell.py');
    if (!shellResponse.ok) {
      throw new Error(`Failed to fetch shell.py (HTTP ${shellResponse.status})`);
    }
    const shellCode = await shellResponse.text();
    await pyodide.runPythonAsync(shellCode);

    // 8. Done
    setStatus('ready', 'Ready');

  } catch (err) {
    console.error('Pyodide init failed:', err);
    setStatus('error', 'Init failed: ' + err.message);
  }
}

// ---------------------------------------------------------------------------
// Command runner
// ---------------------------------------------------------------------------

async function runShellCommand(cmdStr) {
  // run_shell_command() is defined by shell.py which is loaded during init().
  // It returns a JSON string: {"output": "...", "cwd": "..."}.
  const resultJson = await pyodide.runPythonAsync(
    `run_shell_command(${JSON.stringify(cmdStr)})`
  );
  try {
    const { output, cwd } = JSON.parse(resultJson);
    return { output: output ?? '', cwd: cwd ?? '~' };
  } catch (_) {
    // Fallback: treat the raw return value as plain output
    return { output: String(resultJson ?? ''), cwd: '~' };
  }
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
    const { output, cwd } = await runShellCommand(data.command);
    post('result', { output, cwd });
  } catch (err) {
    post('error', { message: String(err) });
  }
};

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
init();
