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
// Pyodide 0.25.x workaround: inject env.__cpp_exception WebAssembly.Tag.
//
// pyodide-build compiles extensions with Emscripten -fwasm-exceptions, so
// every .so imports env.__cpp_exception as a WebAssembly.Tag.  Pyodide 0.25.x
// has a bug where loadDynlib does not include this tag in the import object
// it passes to WebAssembly.instantiate, producing:
//   LinkError: Import "env.__cpp_exception": tag import requires a WebAssembly.Tag
// This was fixed in Pyodide 0.26.0.
//
// IMPORTANT: This patch must be applied BEFORE loadPyodide() is called.
// Emscripten's generated code inside pyodide.asm.js captures a reference to
// WebAssembly.instantiate during module initialisation (which occurs inside
// loadPyodide()).  Patching after loadPyodide() returns is therefore too late
// to intercept the calls made by loadDynlib.
//
// We use a tag holder object so the closure always reads the latest value.
// After loadPyodide() returns we swap the placeholder tag for the one already
// used by Pyodide's Python runtime (pyodide._module.asm.__cpp_exception) so
// that cross-module C++ exception propagation remains correct.
// ---------------------------------------------------------------------------
const _cppExTagHolder = { tag: null };
if (typeof WebAssembly?.Tag !== 'undefined') {
  _cppExTagHolder.tag = new WebAssembly.Tag({ parameters: ['externref'] });
  const _origInstantiate = WebAssembly.instantiate;
  WebAssembly.instantiate = function patchedInstantiate(source, importObject) {
    if (importObject?.env && _cppExTagHolder.tag &&
        !(importObject.env.__cpp_exception instanceof WebAssembly.Tag)) {
      importObject.env.__cpp_exception = _cppExTagHolder.tag;
    }
    return _origInstantiate.call(WebAssembly, source, importObject);
  };
  console.log('[smoke-test] Pre-patched WebAssembly.instantiate (Pyodide 0.25.x wasm-exceptions workaround).');
}

// ---------------------------------------------------------------------------
// Load Pyodide
// ---------------------------------------------------------------------------
console.log('[smoke-test] Loading Pyodide…');
const pyodide = await loadPyodide();
console.log(`[smoke-test] Pyodide ${pyodide.version} ready.`);

// After loading, prefer Pyodide's own __cpp_exception tag for better
// cross-module C++ exception compatibility.
if (_cppExTagHolder.tag) {
  const pyTag = pyodide._module?.asm?.__cpp_exception;
  if (pyTag instanceof WebAssembly.Tag) {
    _cppExTagHolder.tag = pyTag;
    console.log('[smoke-test] Updated wasm-exceptions tag with Pyodide\'s actual __cpp_exception tag.');
  }
}

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
await micropip.install('emfs:///${whlFilename}', deps=False)
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
