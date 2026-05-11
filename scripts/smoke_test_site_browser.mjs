#!/usr/bin/env node
/**
 * smoke_test_site_browser.mjs
 *
 * Serve an assembled _site directory, open it in headless Chromium, wait for
 * worker startup, and run one spack command through the page's runCommand().
 *
 * Usage:
 *   node smoke_test_site_browser.mjs <site-dir>
 */

import { spawn } from 'node:child_process';
import process from 'node:process';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from 'playwright';

const siteDir = process.argv[2];
if (!siteDir) {
  console.error('Usage: smoke_test_site_browser.mjs <site-dir>');
  process.exit(2);
}

const host = '127.0.0.1';
const port = 8765;
const baseUrl = `http://${host}:${port}`;
const SMOKE_COMMAND_TIMEOUT_MS = 180000;

// -u disables Python stdout buffering so the startup message is not held
// in a C-level buffer when stdout is a pipe (the default for child processes).
const server = spawn(
  'python3',
  ['-u', '-m', 'http.server', String(port), '--bind', host, '--directory', siteDir],
  { stdio: ['ignore', 'pipe', 'pipe'] },
);

let serverStderr = '';
server.stderr.on('data', (chunk) => { serverStderr += chunk.toString(); });
server.stdout.on('data', () => {});  // drain stdout to avoid backpressure

// Probe the HTTP server with GET requests rather than scraping startup text.
// This is more reliable across Python versions and output buffering modes.
let serverStarted = false;
for (let i = 0; i < 40 && !serverStarted; i += 1) {
  try {
    const resp = await fetch(`${baseUrl}/index.html`, {
      signal: AbortSignal.timeout(500),
    });
    // Any HTTP response (200, 404, 500…) means the server accepted the
    // connection — that is all we need to confirm it is running.
    if (resp.status > 0) serverStarted = true;
  } catch (_) {
    await delay(250);
  }
}
if (!serverStarted) {
  server.kill('SIGTERM');
  console.error('[browser-smoke] ERROR: local HTTP server did not start.');
  if (serverStderr.trim()) console.error(serverStderr.trim());
  process.exit(1);
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.addInitScript(() => {
  if (typeof window.Terminal === 'undefined') {
    window.Terminal = class {
      constructor() {
        // index.html probes term.textarea to disable mobile autocorrect.
        this.textarea = document.createElement('textarea');
      }
      loadAddon() {}
      open() {}
      write() {}
      onData() {}
      attachCustomKeyEventHandler() {}
      clear() {}
    };
  }
  if (typeof window.FitAddon === 'undefined') {
    window.FitAddon = { FitAddon: class { fit() {} } };
  }
});
const consoleLines = [];
const pageErrors = [];

page.on('console', (msg) => {
  const line = `[${msg.type()}] ${msg.text()}`;
  consoleLines.push(line);
  console.log(`[browser-smoke] console ${line}`);
});
page.on('pageerror', (err) => {
  pageErrors.push(String(err?.message || err));
  console.error(`[browser-smoke] pageerror ${String(err?.message || err)}`);
});

try {
  console.log(`[browser-smoke] Opening ${baseUrl}/index.html`);
  await page.goto(`${baseUrl}/index.html`, { waitUntil: 'load', timeout: 120000 });

  await page.waitForFunction(
    () => {
      const badge = document.querySelector('#status-badge');
      return badge && badge.classList.contains('ready') && badge.textContent.trim() === 'Ready';
    },
    undefined,
    { timeout: 300000 },
  );

  /**
   * Run a single shell command through the page's runCommand() and return its output.
   * Rejects if the command times out.
   */
  async function runSmokeCommand(cmdStr) {
    return page.evaluate(async ({ cmd, timeoutMs }) => {
      const timedResult = await new Promise((resolve, reject) => {
        const timeoutId = setTimeout(() => {
          reject(new Error(`command timed out after ${timeoutMs}ms: ${cmd}`));
        }, timeoutMs);
        runCommand(cmd).then(
          (value) => { clearTimeout(timeoutId); resolve(value); },
          (err)   => { clearTimeout(timeoutId); reject(err); },
        );
      });
      return { output: timedResult?.output ?? '', cwd: timedResult?.cwd ?? '' };
    }, { cmd: cmdStr, timeoutMs: SMOKE_COMMAND_TIMEOUT_MS });
  }

  // --- smoke command 1: spack --version ---
  const versionResult = await runSmokeCommand('spack --version');
  console.log('[browser-smoke] spack --version output:', JSON.stringify(versionResult.output.slice(0, 500)));
  if (!versionResult.output || !versionResult.output.toLowerCase().includes('spack')) {
    throw new Error(`spack --version output missing expected content. Got: ${JSON.stringify(versionResult.output.slice(0, 300))}`);
  }

  // --- smoke command 2: spack spec zlib ---
  // This exercises the clingo concretizer, ProcessPoolExecutor / imap_unordered,
  // and the serial-fallback shims that are needed in the Pyodide/WASM environment.
  // Previously only tested in pytest (CPython, threads available) — this is the
  // first CI check that runs the concretizer inside a real browser/Pyodide context.
  console.log('[browser-smoke] Running spack spec zlib (exercises concretizer)…');
  const specResult = await runSmokeCommand('spack spec zlib');
  console.log('[browser-smoke] spack spec zlib output:', JSON.stringify(specResult.output.slice(0, 800)));
  if (!specResult.output || !specResult.output.toLowerCase().includes('zlib')) {
    throw new Error(`spack spec zlib output missing 'zlib'. Got: ${JSON.stringify(specResult.output.slice(0, 500))}`);
  }
  // The concretizer must not surface thread-constructor or pipe errors.
  const specLower = specResult.output.toLowerCase();
  if (specLower.includes('thread constructor failed') || specLower.includes('errno 52')) {
    throw new Error(`spack spec zlib raised a WASM-incompatible error:\n${specResult.output.slice(0, 800)}`);
  }
  if (specLower.includes('==> error')) {
    throw new Error(`spack spec zlib returned a Spack error:\n${specResult.output.slice(0, 800)}`);
  }

  const joinedLogs = `${consoleLines.join('\n')}\n${pageErrors.join('\n')}`.toLowerCase();
  // Keep these aligned to signatures seen in worker/browser startup failures:
  // wasm EH import mismatch (LinkError / __cpp_exception), explicit init aborts,
  // and top-level unhandled rejections during bootstrap.
  const errorNeedles = ['linkerror', '__cpp_exception', 'init failed', 'unhandled promise rejection'];
  for (const needle of errorNeedles) {
    if (joinedLogs.includes(needle)) {
      throw new Error(`browser console contained runtime error marker: ${needle}`);
    }
  }

  console.log('[browser-smoke] Ready badge reached and both spack commands succeeded.');
} finally {
  await browser.close();
  server.kill('SIGTERM');
}
