/**
 * worker.js — Pyodide Web Worker for Spack-Lite
 *
 * Responsibilities:
 *  1. Load Pyodide (WASM Python runtime)
 *  2. Load wasm-git (libgit2 compiled to WASM) and expose self.gitCall so
 *     that the Python subprocess shim can delegate real git operations
 *  3. Redirect stdout/stderr to the terminal
 *  4. Install clingo 5.7.1 via pyodide.loadPackage('clingo') — bundled in
 *     Pyodide 0.27.3, classic 5.x API required by Spack's ASP solver
 *  5. Fetch and unpack spack-lite.tar.gz into /home/pyodide/spack (MEMFS)
 *  6. Add Spack to sys.path and set SPACK_ROOT / HOME / cwd
 *  7. Inject a fake compiler + package configuration into ~/.spack
 *  8. Execute shim_system.py to monkey-patch os/platform/subprocess and
 *     install stubs for Pyodide-removed modules (termios, tty, readline,
 *     fcntl, grp, pwd)
 *  9. Execute shell.py to install the Python-backed POSIX shell interpreter
 * 10. Receive { type: 'run', command: '...' } messages and return results
 *     including the new working directory after each command
 */

'use strict';

// ---------------------------------------------------------------------------
// CDN URLs — pin to specific versions for reproducibility
// ---------------------------------------------------------------------------
const PYODIDE_CDN  = 'https://cdn.jsdelivr.net/pyodide/v0.27.3/full/pyodide.js';
// wasm-git: libgit2 compiled to WebAssembly (sync browser variant).
// Runs inside this Web Worker, which is the only context where synchronous
// XHR (used for remote git operations) is permitted.
const WASM_GIT_URL = 'https://cdn.jsdelivr.net/npm/wasm-git@0.0.14/lg2.js';

// URL of the stripped-down Spack tarball (relative to the page origin).
// Build it with  scripts/make_spack_lite.sh  and serve it alongside index.html.
const SPACK_LITE_URL = 'spack-lite.tar.gz';

// URL of the full package archive loaded lazily in the browser background.
// Built alongside spack-lite.tar.gz by scripts/make_spack_lite.sh.
// When present, all Spack packages become available after the REPL is already
// active.  When absent (local dev, demo) only the seed packages are available.
const SPACK_PACKAGES_URL = 'spack-packages.tar.gz';

// Base URL for resolving local assets (shim_system.py, shell.py, spack archive,
// clingo wheel).  When running as an inlined Blob URL (local file:// testing),
// self.location.href is a blob:null/… URL and cannot resolve relative paths.
// The build script (scripts/build_local.py) prepends
//   const _LOCAL_BASE_URL = 'file:///…/local/';
// to the inlined worker source, which this variable picks up.
const _WORKER_BASE_URL = (function () {
  if (typeof _LOCAL_BASE_URL !== 'undefined') return _LOCAL_BASE_URL; // injected by build_local.py
  try { return new URL('.', self.location.href).href; } catch (e) { return ''; }
}());

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
let _commandStdoutCaptureChunks = null;

function post(type, payload) {
  if (type === 'stdout' && _commandStdoutCaptureChunks !== null) {
    _commandStdoutCaptureChunks.push(String(payload?.text ?? ''));
    return;
  }
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

// Override the default repos.yaml (which points to a remote git URL) with
// a local path inside the unpacked spack-lite archive.  This prevents spack
// from trying to clone packages from GitHub, which requires git and a live
// network connection — neither of which works in a browser WASM environment.
const REPOS_YAML = `\
repos:
  builtin: $spack/var/spack/repos/spack_repo/builtin
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

const CONCRETIZER_YAML = `\
concretizer:
  # Do not attempt to reuse previously installed packages from the spack
  # database.  In the browser/Pyodide environment no packages are actually
  # installed, so reuse is always false.  More importantly, spack develop
  # (2025+) requires libc information for compiler packages when reusing;
  # the fake gcc stub cannot provide that, causing concretization failures.
  reuse: false
  # The concretization cache is pre-populated at build time by
  # scripts/presolve_packages.py.  A cache hit skips the clingo
  # load/ground/solve phases for seed packages, significantly reducing
  # solve time in the browser WASM environment.
  concretization_cache:
    enable: true
    url: $spack/var/spack/concretization_cache
`;

// ---------------------------------------------------------------------------
// Filesystem helpers (used by wasm-git ↔ Pyodide FS bridging)
// ---------------------------------------------------------------------------

/**
 * Recursively create a directory path in an Emscripten FS instance.
 * Silently skips path components that already exist.
 */
function _mkdirp(fs, path) {
  const parts = path.split('/').filter(Boolean);
  let p = '';
  for (const part of parts) {
    p += '/' + part;
    try { fs.mkdir(p); } catch (e) { /* already exists — ignore */ }
  }
}

/**
 * Recursively copy a directory tree from one Emscripten FS to another.
 * Used to bridge files cloned into wasm-git's MEMFS into Pyodide's MEMFS.
 *
 * @param {object} srcFS   - source Emscripten FS (lg.FS)
 * @param {string} srcPath - absolute path in the source FS
 * @param {object} dstFS   - destination Emscripten FS (pyodide.FS)
 * @param {string} dstPath - absolute path in the destination FS
 */
function _copyTree(srcFS, srcPath, dstFS, dstPath) {
  let stat;
  try {
    stat = srcFS.stat(srcPath);
  } catch (e) {
    return; // source path does not exist
  }
  if (srcFS.isDir(stat.mode)) {
    try { dstFS.mkdir(dstPath); } catch (e) { /* already exists — ignore */ }
    for (const entry of srcFS.readdir(srcPath)) {
      if (entry === '.' || entry === '..') continue;
      _copyTree(srcFS, srcPath + '/' + entry, dstFS, dstPath + '/' + entry);
    }
  } else {
    dstFS.writeFile(dstPath, srcFS.readFile(srcPath));
  }
}

// ---------------------------------------------------------------------------
// Lazy package loader — runs after the REPL is active
// ---------------------------------------------------------------------------

/**
 * Fetch spack-packages.tar.gz and unpack it into Pyodide's MEMFS so the full
 * Spack package set becomes available.
 *
 * NOT called automatically on startup to avoid memory pressure during
 * concretization.  Triggered on demand via `spack load-packages` in the shell
 * (which calls the exposed `self.loadPackages` JS hook) or via a
 * { type: 'load-packages' } worker message.
 *
 * The archive must have the same structure as spack-lite.tar.gz
 * (top-level directory "spack/") so it can be extracted with
 * extractDir: '/home/pyodide' and merge into the existing tree.
 *
 * Errors are logged but never re-thrown.
 */
async function loadPackagesBackground() {
  try {
    const url = new URL(SPACK_PACKAGES_URL, _WORKER_BASE_URL).href;
    // Initiate the fetch first (before changing the badge) so that the
    // 'ready' status message reaches the main thread before we send
    // 'packages-loading'.  This guarantees startREPL() fires before the
    // badge changes to 'packages-loading'.
    const response = await fetch(url);
    if (!response.ok) {
      // File not present — silent fallback; only seed packages are available.
      console.info(
        'spack-packages.tar.gz not found — full package set not loaded.',
      );
      return;
    }
    setStatus('packages-loading', 'Loading packages\u2026');
    let buffer = await response.arrayBuffer();
    // Defer the (synchronous) unpack until no Python command is running so
    // we do not modify the filesystem while Python is mid-execution.
    while (_commandStdoutCaptureChunks !== null) {
      await new Promise(r => setTimeout(r, 50));
    }
    pyodide.unpackArchive(buffer, 'gztar', { extractDir: '/home/pyodide' });
    // Release the compressed ArrayBuffer immediately after extraction so that
    // the JS heap does not hold both the archive and subsequent spack spec
    // working sets simultaneously.
    buffer = null;
    // Invalidate Spack's in-memory package repository caches so that the
    // newly extracted packages are visible to subsequent spack commands.
    // Without this, FastPackageChecker still holds the old on-disk snapshot
    // and RepoPath._all_package_names keeps its memoized (lru_cache) result.
    await pyodide.runPythonAsync(`
try:
    import spack.repo as _repo
    for _r in _repo.PATH.repos:
        if hasattr(_r, '_pkg_checker'):
            _r._pkg_checker.invalidate()
    if hasattr(_repo.RepoPath._all_package_names, 'cache_clear'):
        _repo.RepoPath._all_package_names.cache_clear()
    if hasattr(_repo.RepoPath._all_package_names_set, 'cache_clear'):
        _repo.RepoPath._all_package_names_set.cache_clear()
except Exception:
    pass
`);
    setStatus('ready', 'Ready');
    post('stdout', { text: '\n\x1b[2m[spack-lite] Full package set loaded.\x1b[0m\n' });
  } catch (err) {
    console.warn('Background package loading failed:', err);
    // Restore ready state in case we changed the badge to packages-loading.
    setStatus('ready', 'Ready');
  }
}

/**
 * Public hook callable from Python via `js.loadPackages()`.
 * Fires loadPackagesBackground() without blocking the caller.
 */
self.loadPackages = () => loadPackagesBackground();

// ---------------------------------------------------------------------------
// Main initialisation
// ---------------------------------------------------------------------------
let pyodide = null;
let lg      = null;  // wasm-git module handle (null until loaded)

// Interrupt buffer passed from the main thread via { type: 'set-interrupt-buffer' }.
// Stored here so it can be applied once Pyodide is ready, even if the message
// arrives before init() completes.
let _pendingInterruptBuffer = null;

async function init() {
  try {
    // 1. Load Pyodide
    setStatus('loading', 'Loading Pyodide…');
    importScripts(PYODIDE_CDN);
    pyodide = await loadPyodide();

    // Apply any interrupt buffer that arrived before Pyodide was ready.
    if (_pendingInterruptBuffer) {
      pyodide.setInterruptBuffer(_pendingInterruptBuffer);
    }

    // 2. Load wasm-git (libgit2 compiled to WASM, sync browser variant).
    //    The sync variant uses synchronous XHR, which is only permitted
    //    inside a Web Worker — exactly where we are.
    //    On failure we log a warning and continue; the subprocess shim in
    //    shim_system.py will fall back to canned mock responses for git.
    setStatus('loading', 'Loading wasm-git…');
    try {
      // Capture wasm-git stdout/stderr via module overrides (must be set
      // on globalThis BEFORE the module is imported so that the Emscripten
      // runtime picks them up during initialisation).
      let _gitOutputLines = [];
      let _gitErrLines    = [];
      globalThis.wasmGitModuleOverrides = {
        print:    (line) => { _gitOutputLines.push(line); },
        printErr: (line) => { _gitErrLines.push(line); },
      };

      const lg2mod = await import(WASM_GIT_URL);
      lg = await lg2mod.default();

      // Provide a minimal .gitconfig so libgit2 does not abort on missing
      // user identity when committing (spack fetch does not commit, but
      // having it avoids unnecessary warnings).
      lg.FS.writeFile(
        '/home/web_user/.gitconfig',
        '[user]\n  name = Spack Browser\n  email = spack@browser.local\n'
      );

      /**
       * Execute a git command via wasm-git and return a JSON result object.
       *
       * Called synchronously from Python through Pyodide's `js` module:
       *   js.gitCall(json.dumps(['clone', url, dest]))
       *
       * For `git clone`, the cloned tree is also copied from wasm-git's
       * MEMFS into Pyodide's MEMFS so that Python can see the files.
       *
       * @param  {string} argsJson  JSON array of git argv (without 'git').
       * @return {string}           JSON string: {stdout, stderr, returncode}.
       */
      self.gitCall = function gitCall(argsJson) {
        const args = JSON.parse(argsJson);
        const isClone = args[0] === 'clone';
        // A clone with a destination path requires at least 3 args:
        //   ['clone', '<url>', '<destPath>']
        // When only the URL is given git infers the destination from it;
        // in that case we cannot pre-create dirs or bridge the FS, so we
        // only activate FS bridging when an explicit destination is present.
        const destPath = isClone && args.length >= 3 ? args[args.length - 1] : null;
        const parentDir = destPath ? destPath.substring(0, destPath.lastIndexOf('/')) : null;

        // For clone, ensure the destination's parent directory exists in
        // wasm-git's FS before git tries to create the repo directory.
        if (parentDir) _mkdirp(lg.FS, parentDir);

        _gitOutputLines = []; // reset capture buffers for this call
        _gitErrLines    = [];
        let returncode = 0;
        try {
          lg.callMain(args);
        } catch (e) {
          // Emscripten calls exit() when git finishes; this surfaces as an
          // ExitStatus object with a numeric .status field.  Any non-zero
          // status means git itself reported a failure.
          if (e && typeof e.status === 'number') {
            returncode = e.status;
          } else if (e) {
            // Unexpected JS error — treat as a generic failure.
            returncode = 1;
            _gitErrLines.push(e.message || String(e));
            if (e.stack) _gitErrLines.push(e.stack);
          }
        }
        const stdout = _gitOutputLines.join('\n') + (_gitOutputLines.length ? '\n' : '');
        const stderr = _gitErrLines.join('\n')    + (_gitErrLines.length    ? '\n' : '');

        // After clone, bridge the cloned tree from wasm-git's MEMFS into
        // Pyodide's MEMFS so Python / spack can read the files.
        // Only attempt the copy when an explicit destination was given AND
        // the directory actually exists in wasm-git's FS (i.e. clone worked).
        if (destPath !== null) {
          let cloneSucceeded = false;
          try { cloneSucceeded = lg.FS.isDir(lg.FS.stat(destPath).mode); } catch (e) { /* ignore */ }
          if (cloneSucceeded) {
            if (parentDir) _mkdirp(pyodide.FS, parentDir);
            try {
              _copyTree(lg.FS, destPath, pyodide.FS, destPath);
            } catch (e) {
              console.warn('wasm-git: FS bridge failed after clone:', e);
            }
          }
        }

        return JSON.stringify({ stdout, stderr, returncode });
      };
    } catch (gitErr) {
      console.warn('wasm-git unavailable — git operations will use mock responses:', gitErr);
    }

    // 3. Redirect stdout/stderr to the terminal
    await pyodide.runPythonAsync(STDOUT_REDIRECT);

    // 4. Install clingo.  Pyodide 0.27.3 bundles clingo 5.7.1 (cffi-based,
    //    classic 5.x Python API) as a regular loadPackage() target.  This is
    //    the API that Spack's solver (spack/solver/asp.py) expects:
    //    clingo.Symbol, clingo.Control(args), clingo.ast.parse_files(files, cb).
    //    loadPackage() fetches directly from the Pyodide CDN so no external
    //    wheel fetch or WebAssembly.instantiate patching is needed.
    //    On failure we log a warning; Spack will fall through to its own
    //    bootstrap path and report a more specific error at concretization time.
    setStatus('loading', 'Installing clingo…');
    try {
      await pyodide.loadPackage('clingo');
    } catch (clingoErr) {
      console.warn(
        'clingo load failed — bootstrap will be attempted by Spack:',
        clingoErr?.message || String(clingoErr),
      );
    }

    // 5. Fetch and unpack spack-lite.tar.gz
    setStatus('loading', 'Fetching spack-lite archive…');
    try {
      const response = await fetch(new URL(SPACK_LITE_URL, _WORKER_BASE_URL).href, { cache: 'no-cache' });
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

    // 6. Add Spack to sys.path (if unpacked) and set up the environment
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

# Start in the spack root directory (only when unpacked; fall back to HOME in demo mode)
if os.path.isdir('/home/pyodide/spack'):
    os.chdir('/home/pyodide/spack')
else:
    os.chdir('/home/pyodide')
`);

    // 7. Create ~/.spack configuration directories
    await pyodide.runPythonAsync(`
import os

spack_cfg_linux = '/home/pyodide/.spack/linux'
os.makedirs(spack_cfg_linux, exist_ok=True)

cfg_files = {
    '/home/pyodide/.spack/config.yaml': ${JSON.stringify(CONFIG_YAML)},
    '/home/pyodide/.spack/concretizer.yaml': ${JSON.stringify(CONCRETIZER_YAML)},
    '/home/pyodide/.spack/linux/compilers.yaml': ${JSON.stringify(COMPILERS_YAML)},
    '/home/pyodide/.spack/packages.yaml': ${JSON.stringify(PACKAGES_YAML)},
    '/home/pyodide/.spack/repos.yaml': ${JSON.stringify(REPOS_YAML)},
}
for path, content in cfg_files.items():
    with open(path, 'w') as f:
        f.write(content)
`);

    // 8. Load and execute the system shim
    // shim_system.py is the single canonical source for all module shims.
    // It is served from the same origin as worker.js; if it cannot be
    // fetched the worker raises an error rather than continuing with a
    // partial / out-of-date fallback.
    setStatus('loading', 'Applying system shims…');
    const shimResponse = await fetch(new URL('shim_system.py', _WORKER_BASE_URL).href);
    if (!shimResponse.ok) {
      throw new Error(`Failed to fetch shim_system.py (HTTP ${shimResponse.status})`);
    }
    const shimCode = await shimResponse.text();
    await pyodide.runPythonAsync(shimCode);

    // 9. Load and execute the shell interpreter
    // shell.py defines run_shell_command() which handles built-in POSIX-like
    // commands (ls, cd, cat, grep, …) and routes `spack` through the Spack
    // Python API.  It must be loaded after shim_system.py so that the module
    // shims are in place before any spack imports are attempted.
    setStatus('loading', 'Loading shell…');
    const shellResponse = await fetch(new URL('shell.py', _WORKER_BASE_URL).href);
    if (!shellResponse.ok) {
      throw new Error(`Failed to fetch shell.py (HTTP ${shellResponse.status})`);
    }
    const shellCode = await shellResponse.text();
    await pyodide.runPythonAsync(shellCode);

    // 10. Done
    setStatus('ready', 'Ready');

    // Note: spack-packages.tar.gz (full package set) is NOT loaded
    // automatically to prevent memory exhaustion during `spack spec`.
    // Users can load it on demand with:  spack load-packages

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
  if (data.type === 'set-interrupt-buffer') {
    // Wired up by index.html after SharedArrayBuffer becomes available
    // (requires COOP/COEP headers, provided by coi-serviceworker).
    // Calling pyodide.setInterruptBuffer() lets the main thread raise
    // KeyboardInterrupt in Python by writing 2 to interruptBuffer[0].
    _pendingInterruptBuffer = data.buffer;
    if (pyodide && data.buffer) {
      pyodide.setInterruptBuffer(data.buffer);
    }
    return;
  }
  if (data.type === 'load-packages') {
    loadPackagesBackground();
    return;
  }
  if (data.type !== 'run') return;

  if (!pyodide) {
    post('error', { message: 'Pyodide is not ready yet.' });
    return;
  }
  if (_commandStdoutCaptureChunks !== null) {
    post('error', { message: 'Another command is already running.' });
    return;
  }

  try {
    _commandStdoutCaptureChunks = [];
    const { output, cwd } = await runShellCommand(data.command);
    const capturedStdout = _commandStdoutCaptureChunks.join('');
    // runShellCommand() should return a string output; when it returns an empty
    // string, fall back to stdout captured from the Pyodide writer so browser
    // callers still receive command text.
    const hasStructuredOutput = typeof output === 'string' && output !== '';
    const mergedOutput = hasStructuredOutput ? output : capturedStdout;
    post('result', { output: mergedOutput, cwd });
  } catch (err) {
    post('error', { message: String(err) });
  } finally {
    _commandStdoutCaptureChunks = null;
  }
};

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
init();
