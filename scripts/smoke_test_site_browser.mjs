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

const server = spawn(
  'python3',
  ['-m', 'http.server', String(port), '--bind', host, '--directory', siteDir],
  { stdio: ['ignore', 'pipe', 'pipe'] },
);

let serverStarted = false;
let serverStderr = '';
server.stdout.on('data', (chunk) => {
  const text = chunk.toString();
  if (!serverStarted && text.toLowerCase().includes('serving http')) serverStarted = true;
});
server.stderr.on('data', (chunk) => {
  const text = chunk.toString();
  serverStderr += text;
  if (!serverStarted && text.toLowerCase().includes('serving http')) serverStarted = true;
});

for (let i = 0; i < 40 && !serverStarted; i += 1) {
  await delay(250);
}
if (!serverStarted) {
  server.kill('SIGTERM');
  console.error('[browser-smoke] ERROR: local HTTP server did not start.');
  if (serverStderr.trim()) console.error(serverStderr.trim());
  process.exit(1);
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
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

  const result = await page.evaluate(async () => {
    const value = await runCommand('spack spec zlib');
    return { output: value?.output ?? '', cwd: value?.cwd ?? '' };
  });

  if (!result.output || !result.output.includes('zlib')) {
    throw new Error('spack spec zlib output missing expected content');
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

  console.log('[browser-smoke] Ready badge reached and spack command succeeded.');
} finally {
  await browser.close();
  server.kill('SIGTERM');
}
