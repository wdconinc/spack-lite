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
 *   1. Import clingo and read its __version__ string.
 *   2. Solve a simple ASP program "{a}." — must yield exactly 2 models.
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
pyodide.FS.writeFile('/clingo.whl', whlBytes);

console.log('[smoke-test] Installing clingo from emfs:///clingo.whl…');
await pyodide.runPythonAsync(`
import micropip
await micropip.install('emfs:///clingo.whl')
`);
console.log('[smoke-test] clingo installed.');

// ---------------------------------------------------------------------------
// Smoke test 1: import clingo and check __version__
// ---------------------------------------------------------------------------
const version = await pyodide.runPythonAsync(`
import clingo
clingo.__version__
`);
console.log(`[smoke-test] clingo version: ${version}`);

// ---------------------------------------------------------------------------
// Smoke test 2: run a minimal ASP solve.
// The program "{a}." has two stable models: {} and {a}.
// ---------------------------------------------------------------------------
console.log('[smoke-test] Running ASP solve smoke test…');
const nModels = await pyodide.runPythonAsync(`
import clingo
ctl = clingo.Control()
ctl.add("base", [], "{a}.")
ctl.ground([("base", [])])
models = []
ctl.solve(on_model=lambda m: models.append(str(m)))
len(models)
`);
console.log(`[smoke-test] Solve yielded ${nModels} model(s) (expected 2).`);
if (nModels !== 2) {
  console.error(`[smoke-test] ERROR: expected 2 models, got ${nModels}`);
  process.exit(1);
}

console.log('[smoke-test] All checks passed.');
