#!/usr/bin/env node
/**
 * smoke_test_clingo_pyodide.mjs
 *
 * Load clingo 5.7.1 in a real Pyodide (Node.js) runtime and verify that the
 * classic clingo 5.x Python API works correctly.  This is the API that Spack's
 * solver (spack/solver/asp.py) uses.
 *
 * Usage (run from the directory where pyodide was installed via npm):
 *   node smoke_test_clingo_pyodide.mjs [wheel-dir-or-file]
 *
 * If a wheel path is provided the local file is installed; otherwise the
 * pre-built clingo 5.7.1 wheel is downloaded directly from the Pyodide CDN
 * (the same URL that worker.js uses at runtime).
 *
 * The script imports 'pyodide' from node_modules/ in the current working
 * directory; run from the directory where `npm install pyodide` was executed.
 *
 * Tests performed:
 *   1. Import clingo and assert clingo.__version__ is a non-empty string.
 *   2. Assert clingo.Symbol is accessible at the top level (Spack checks this).
 *   3. Solve a simple SAT program "{a}." — must yield exactly 2 models.
 *   4. Solve an UNSAT program "a :- not a." — must yield 0 models.
 *   5. Solve with numeric atoms over a range — verify model count.
 */

import { loadPyodide } from 'pyodide';
import { readFileSync, readdirSync, statSync } from 'fs';
import { resolve, basename } from 'path';

// ---------------------------------------------------------------------------
// The canonical CDN wheel used by worker.js (clingo 5.7.1, pyodide_2024_0 ABI)
// ---------------------------------------------------------------------------
const CLINGO_WHEEL_URL = 'https://cdn.jsdelivr.net/pyodide/v0.27.3/full/clingo-5.7.1-cp312-cp312-pyodide_2024_0_wasm32.whl';

// ---------------------------------------------------------------------------
// Resolve wheel source: local path from CLI arg, or CDN URL
// ---------------------------------------------------------------------------
const arg = process.argv[2];
let clingoWheelSource = CLINGO_WHEEL_URL;   // default: CDN
let whlLocalPath = null;

if (arg) {
  try {
    const st = statSync(arg);
    if (st.isDirectory()) {
      const whls = readdirSync(arg).filter(f => f.endsWith('.whl'));
      if (whls.length === 0) {
        console.error(`ERROR: no .whl file found in ${arg}`);
        process.exit(1);
      }
      whlLocalPath = resolve(arg, whls[0]);
    } else {
      whlLocalPath = resolve(arg);
    }
    clingoWheelSource = `emfs:///${basename(whlLocalPath)}`;
    console.log(`[smoke-test] Using local wheel: ${basename(whlLocalPath)}`);
  } catch (err) {
    console.error(`ERROR: cannot access path '${arg}': ${err.message}`);
    process.exit(1);
  }
} else {
  console.log(`[smoke-test] No local wheel specified — using CDN: ${CLINGO_WHEEL_URL}`);
}

// ---------------------------------------------------------------------------
// Load Pyodide
// ---------------------------------------------------------------------------
console.log('[smoke-test] Loading Pyodide…');
const pyodide = await loadPyodide();
console.log(`[smoke-test] Pyodide ${pyodide.version} ready.`);

// ---------------------------------------------------------------------------
// If using a local wheel, write it into the Emscripten FS first.
// ---------------------------------------------------------------------------
if (whlLocalPath) {
  const whlBytes = readFileSync(whlLocalPath);
  pyodide.FS.writeFile(`/${basename(whlLocalPath)}`, whlBytes);
  console.log(`[smoke-test] Wrote ${basename(whlLocalPath)} to emfs.`);
}

// ---------------------------------------------------------------------------
// Load cffi (clingo 5.7.1 is cffi-based) then install clingo via micropip.
// cffi is bundled as a Pyodide package so loadPackage() uses the local copy.
// ---------------------------------------------------------------------------
await pyodide.loadPackage(['cffi', 'micropip']);

console.log(`[smoke-test] Installing clingo from ${clingoWheelSource}…`);
try {
  await pyodide.runPythonAsync(`
import micropip
await micropip.install('${clingoWheelSource}', deps=False)
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
console.log(`[smoke-test] clingo.__version__: ${version}`);
if (!version || typeof version !== 'string' || version.trim() === '') {
  console.error('[smoke-test] ERROR: clingo.__version__ is empty or undefined');
  process.exit(1);
}

// ---------------------------------------------------------------------------
// Smoke test 2: clingo.Symbol accessible at top level (Spack bootstrap check)
// ---------------------------------------------------------------------------
let hasSymbol;
try {
  hasSymbol = await pyodide.runPythonAsync(`
import clingo
hasattr(clingo, 'Symbol')
`);
} catch (err) {
  console.error(`[smoke-test] ERROR: symbol check failed: ${err}`);
  process.exit(1);
}
if (!hasSymbol) {
  console.error('[smoke-test] ERROR: clingo.Symbol not accessible at top level (Spack bootstrap check would fail)');
  process.exit(1);
}
console.log('[smoke-test] clingo.Symbol accessible at top level. ✓');

// ---------------------------------------------------------------------------
// Smoke test 3: run a minimal ASP solve (classic 5.x API: Control, add, ground, solve)
// The program "{a}." has two stable models: {} and {a}.
// ---------------------------------------------------------------------------
console.log('[smoke-test] Running ASP solve smoke test (classic 5.x API)…');
let nModels;
try {
  nModels = await pyodide.runPythonAsync(`
import clingo
ctl = clingo.Control(["0"])
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
// Smoke test 4: unsatisfiable program should yield 0 models.
// ---------------------------------------------------------------------------
console.log('[smoke-test] Running UNSAT smoke test…');
let nUnsatModels;
try {
  nUnsatModels = await pyodide.runPythonAsync(`
import clingo
ctl = clingo.Control(["0"])
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
// Smoke test 5: solve with numeric atoms over a range.
// "{a(1..3)}." has 2^3 = 8 stable models (all subsets of {a(1),a(2),a(3)}).
// ---------------------------------------------------------------------------
console.log('[smoke-test] Running parameterized program test…');
let nParamModels;
try {
  nParamModels = await pyodide.runPythonAsync(`
import clingo
ctl = clingo.Control(["0"])
ctl.add("base", [], "#program base. {a(1..3)}.")
ctl.ground([("base", [])])
models = []
ctl.solve(on_model=lambda m: models.append([str(s) for s in m.symbols(shown=True)]))
len(models)
`);
} catch (err) {
  console.error(`[smoke-test] ERROR: parameterized program test failed: ${err}`);
  process.exit(1);
}
console.log(`[smoke-test] Parameterized solve yielded ${nParamModels} model(s) (expected 8).`);
if (nParamModels !== 8) {
  console.error(`[smoke-test] ERROR: expected 8 models (subsets of {a(1),a(2),a(3)}), got ${nParamModels}`);
  process.exit(1);
}

console.log('[smoke-test] All checks passed.');

