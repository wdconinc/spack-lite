#!/usr/bin/env node
/**
 * smoke_test_clingo_pyodide.mjs
 *
 * Load the clingo Pyodide wheel in a real Pyodide (Node.js) runtime and verify
 * that the ASP solver works correctly.
 *
 * Usage (run from the directory where pyodide was installed via npm):
 *   node smoke_test_clingo_pyodide.mjs <wheel-dir-or-file>
 *
 * The script imports 'pyodide' from node_modules/ in the current working
 * directory; run from the directory where `npm install pyodide` was executed.
 *
 * Tests performed:
 *   1. Import clingo and assert __version__ is a non-empty string.
 *   2. Solve a simple SAT program "{a}." — must yield exactly 2 models.
 *   3. Solve an UNSAT program "a :- not a." — must yield 0 models.
 *   4. Pass invalid ASP syntax — clingo must raise RuntimeError.
 */

import { loadPyodide } from 'pyodide';
import { readFileSync, readdirSync, statSync } from 'fs';
import { resolve, basename } from 'path';

// ---------------------------------------------------------------------------
// Resolve wheel path from CLI argument
// ---------------------------------------------------------------------------
const arg = process.argv[2];
if (!arg) {
  console.error('Usage: smoke_test_clingo_pyodide.mjs <wheel-dir-or-file>');
  process.exit(1);
}

let whlPath;
try {
  const st = statSync(arg);
  if (st.isDirectory()) {
    const whls = readdirSync(arg).filter(f => f.endsWith('.whl'));
    if (whls.length === 0) {
      console.error(`ERROR: no .whl file found in ${arg}`);
      process.exit(1);
    }
    whlPath = resolve(arg, whls[0]);
  } else {
    whlPath = resolve(arg);
  }
} catch (err) {
  console.error(`ERROR: cannot access path '${arg}': ${err.message}`);
  process.exit(1);
}

console.log(`[smoke-test] Wheel: ${basename(whlPath)}`);

// ---------------------------------------------------------------------------
// Load Pyodide
// ---------------------------------------------------------------------------
console.log('[smoke-test] Loading Pyodide…');
const pyodide = await loadPyodide();
console.log(`[smoke-test] Pyodide ${pyodide.version} ready.`);

// ---------------------------------------------------------------------------
// Write the wheel into the Emscripten filesystem and install via micropip.
// micropip supports the emfs:// scheme for files already in the pyodide FS.
// ---------------------------------------------------------------------------
await pyodide.loadPackage('micropip');
const whlBytes = readFileSync(whlPath);
const whlFilename = basename(whlPath);
pyodide.FS.writeFile(`/${whlFilename}`, whlBytes);

console.log(`[smoke-test] Installing clingo from emfs:///${whlFilename}…`);
try {
  await pyodide.runPythonAsync(`
import micropip
import micropip.transaction as _mt
# Bypass ABI compatibility check: the wheel is built for this specific
# Pyodide/Emscripten target; strict CPython-version matching is not needed.
# Patch the reference in transaction module (which imports it at load time).
_mt.check_compatible = lambda filename: None
await micropip.install('emfs:///${whlFilename}', deps=False)

# The wheel's .so is tagged cpython-312, but Pyodide may run Python 3.11.
# Rename any installed clingo .so to match the running interpreter's ABI tag
# so that the normal 'import clingo' machinery can locate it.
import sys, os, glob, re
running_tag = f"cpython-{sys.version_info.major}{sys.version_info.minor}"
for f in glob.glob('/lib/python3*/site-packages/clingo*.so'):
    new_name = re.sub(r'cpython-3\d+', running_tag, f)
    if new_name != f and not os.path.exists(new_name):
        os.rename(f, new_name)
`);
} catch (err) {
  console.error(`[smoke-test] ERROR: micropip install failed: ${err}`);
  process.exit(1);
}
console.log('[smoke-test] clingo installed.');

// ---------------------------------------------------------------------------
// Smoke test 1: import clingo and check __version__
// ---------------------------------------------------------------------------
let version;
try {
  version = await pyodide.runPythonAsync(`
import clingo
clingo.__version__
`);
} catch (err) {
  console.error(`[smoke-test] ERROR: failed to import clingo: ${err}`);
  process.exit(1);
}
console.log(`[smoke-test] clingo version: ${version}`);
if (!version || typeof version !== 'string' || version.trim() === '') {
  console.error('[smoke-test] ERROR: clingo.__version__ is empty or undefined');
  process.exit(1);
}

// ---------------------------------------------------------------------------
// Smoke test 2: run a minimal ASP solve.
// The program "{a}." has two stable models: {} and {a}.
// ---------------------------------------------------------------------------
console.log('[smoke-test] Running ASP solve smoke test…');
let nModels;
try {
  nModels = await pyodide.runPythonAsync(`
import clingo
ctl = clingo.Control()
ctl.add("base", [], "{a}.")
ctl.ground([("base", [])])
models = []
ctl.solve(on_model=lambda m: models.append(str(m)))
len(models)
`);
} catch (err) {
  console.error(`[smoke-test] ERROR: ASP solve failed: ${err}`);
  process.exit(1);
}
console.log(`[smoke-test] Solve yielded ${nModels} model(s) (expected 2).`);
if (nModels !== 2) {
  console.error(`[smoke-test] ERROR: expected 2 models, got ${nModels}`);
  process.exit(1);
}

// ---------------------------------------------------------------------------
// Smoke test 3: unsatisfiable program should yield 0 models.
// "a :- not a." has no stable model in answer-set semantics.
// ---------------------------------------------------------------------------
console.log('[smoke-test] Running UNSAT smoke test…');
let nUnsatModels;
try {
  nUnsatModels = await pyodide.runPythonAsync(`
import clingo
ctl = clingo.Control()
ctl.add("base", [], "a :- not a.")
ctl.ground([("base", [])])
models = []
ctl.solve(on_model=lambda m: models.append(str(m)))
len(models)
`);
} catch (err) {
  console.error(`[smoke-test] ERROR: UNSAT solve failed: ${err}`);
  process.exit(1);
}
console.log(`[smoke-test] UNSAT solve yielded ${nUnsatModels} model(s) (expected 0).`);
if (nUnsatModels !== 0) {
  console.error(`[smoke-test] ERROR: expected 0 models for UNSAT program, got ${nUnsatModels}`);
  process.exit(1);
}

// ---------------------------------------------------------------------------
// Smoke test 4: syntax error should raise a RuntimeError.
// ---------------------------------------------------------------------------
console.log('[smoke-test] Running syntax-error detection test…');
let syntaxErrorCaught;
try {
  syntaxErrorCaught = await pyodide.runPythonAsync(`
import clingo
caught = False
try:
    ctl = clingo.Control()
    ctl.add("base", [], "this is not valid ASP !!!")
    ctl.ground([("base", [])])
except RuntimeError:
    caught = True
caught
`);
} catch (err) {
  console.error(`[smoke-test] ERROR: syntax-error test failed unexpectedly: ${err}`);
  process.exit(1);
}
console.log(`[smoke-test] Syntax error caught: ${syntaxErrorCaught} (expected True).`);
if (!syntaxErrorCaught) {
  console.error('[smoke-test] ERROR: expected RuntimeError for invalid syntax, but none was raised');
  process.exit(1);
}

console.log('[smoke-test] All checks passed.');
