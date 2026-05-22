/**
 * spack_run.js — Spack-Lite interactive code block runner
 *
 * Scans for [data-spack-runnable] wrappers injected by sphinx_spack_lite
 * and adds a "Run ▶" button to each.  On first click the spack-lite Web
 * Worker is created lazily (zero overhead at page-load time).  The same
 * worker instance is reused for all blocks on the page so the Spack
 * environment (working directory, installed packages, etc.) persists
 * across block executions within a single page visit.
 *
 * Worker message protocol (worker.js):
 *   IN  { type: 'configure', spackLiteUrl, spackPackagesUrl }
 *   IN  { type: 'run', command: '<bash string>' }
 *   OUT { type: 'status', state, message }
 *   OUT { type: 'stdout', text }
 *   OUT { type: 'result', output, cwd }
 *   OUT { type: 'error', message }
 */

(function () {
  'use strict';

  const config = window.SPACK_LITE_CONFIG || {};

  // ---------------------------------------------------------------------------
  // Worker state (shared across all blocks on the page)
  // ---------------------------------------------------------------------------

  /** @type {Worker|null} */
  let worker = null;
  /** True once the worker has posted { type: 'status', state: 'ready' } */
  let workerReady = false;
  /** Reject functions for pending initialisation waiters */
  let readyResolvers = [];
  let readyRejecters = [];

  /**
   * Promise-based FIFO queue so commands execute one at a time.
   * Each entry is { command, outputEl, buttonEl }.
   */
  let commandQueue = [];
  let queueRunning = false;

  // The output element for the currently-running command (receives stdout).
  /** @type {HTMLElement|null} */
  let activeOutputEl = null;

  // ---------------------------------------------------------------------------
  // Worker initialisation
  // ---------------------------------------------------------------------------

  function initWorker() {
    if (worker) return;

    const workerUrl = config.workerUrl || '_static/worker.js';
    worker = new Worker(workerUrl);

    // Immediately send configure so the worker can override asset URLs before
    // it fetches spack-lite.tar.gz (which happens only after Pyodide + clingo
    // finish loading — several seconds into startup).
    worker.postMessage({
      type: 'configure',
      spackLiteUrl: config.spackLiteUrl || '_static/spack-lite.tar.gz',
      spackPackagesUrl: config.spackPackagesUrl || '_static/spack-packages.tar.gz',
    });

    worker.onmessage = function ({ data }) {
      switch (data.type) {
        case 'status':
          handleStatus(data.state, data.message);
          break;
        case 'stdout':
          if (activeOutputEl) appendOutput(activeOutputEl, data.text);
          break;
        case 'result':
          handleResult(data.output);
          break;
        case 'error':
          handleError(data.message);
          break;
      }
    };

    worker.onerror = function (err) {
      const msg = err.message || String(err);
      workerReady = false;
      readyRejecters.forEach(fn => fn(new Error(msg)));
      readyRejecters = [];
      readyResolvers = [];
      // Reject any in-flight runCommandAsync promise so drainQueue() can unwind.
      if (resultReject) {
        resultReject(new Error(msg));
        resultResolve = null;
        resultReject = null;
      }
      if (activeOutputEl) {
        appendOutput(activeOutputEl, '\n\x1b[31mWorker error: ' + msg + '\x1b[0m\n', true);
        finaliseOutput(activeOutputEl);
        activeOutputEl = null;
      }
      drainQueue(/*error=*/true);
    };
  }

  function handleStatus(state, message) {
    updateStatusBadge(state, message);
    if (state === 'ready') {
      workerReady = true;
      readyResolvers.forEach(fn => fn());
      readyResolvers = [];
      readyRejecters = [];
      drainQueue();
    } else if (state === 'error') {
      readyRejecters.forEach(fn => fn(new Error(message)));
      readyRejecters = [];
      readyResolvers = [];
      drainQueue(/*error=*/true);
    }
  }

  function waitForWorkerReady() {
    if (workerReady) return Promise.resolve();
    return new Promise((resolve, reject) => {
      readyResolvers.push(resolve);
      readyRejecters.push(reject);
    });
  }

  // ---------------------------------------------------------------------------
  // Command queue
  // ---------------------------------------------------------------------------

  /**
   * Enqueue a command execution.
   * @param {string} command
   * @param {HTMLElement} outputEl
   * @param {HTMLElement} buttonEl
   */
  function enqueueCommand(command, outputEl, buttonEl) {
    commandQueue.push({ command, outputEl, buttonEl });
    if (!queueRunning) drainQueue();
  }

  async function drainQueue(error) {
    if (queueRunning) return;
    queueRunning = true;

    while (commandQueue.length > 0) {
      const { command, outputEl, buttonEl } = commandQueue.shift();

      setButtonState(buttonEl, 'running');
      outputEl.style.display = 'block';
      clearOutput(outputEl);

      if (error) {
        appendOutput(outputEl, '\n[spack-lite] Worker failed to initialise.\n', true);
        finaliseOutput(outputEl);
        setButtonState(buttonEl, 'done');
        continue;
      }

      try {
        await waitForWorkerReady();
        activeOutputEl = outputEl;
        // runCommandAsync returns a promise resolved by handleResult/handleError
        await runCommandAsync(command);
      } catch (err) {
        appendOutput(outputEl, '\n[spack-lite] Error: ' + err.message + '\n', true);
        finaliseOutput(outputEl);
      }

      setButtonState(buttonEl, 'done');
      activeOutputEl = null;
    }

    queueRunning = false;
  }

  /** Promise resolved/rejected by the next result/error message. */
  let resultResolve = null;
  let resultReject = null;

  function runCommandAsync(command) {
    return new Promise((resolve, reject) => {
      resultResolve = resolve;
      resultReject = reject;
      worker.postMessage({ type: 'run', command });
    });
  }

  function handleResult(output) {
    if (activeOutputEl && output) {
      appendOutput(activeOutputEl, output);
    }
    if (activeOutputEl) finaliseOutput(activeOutputEl);
    if (resultResolve) { resultResolve(); resultResolve = null; resultReject = null; }
  }

  function handleError(message) {
    if (activeOutputEl) {
      appendOutput(activeOutputEl, '\n[spack-lite] ' + message + '\n', true);
      finaliseOutput(activeOutputEl);
    }
    if (resultReject) { resultReject(new Error(message)); resultResolve = null; resultReject = null; }
  }

  // ---------------------------------------------------------------------------
  // Output area helpers
  // ---------------------------------------------------------------------------

  function clearOutput(el) {
    el.textContent = '';
  }

  function appendOutput(el, text, isError) {
    // Strip ANSI escape codes for plain display (no xterm.js dependency here).
    const clean = text.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '');
    if (isError) {
      const span = document.createElement('span');
      span.className = 'spack-run-error';
      span.textContent = clean;
      el.appendChild(span);
    } else {
      el.appendChild(document.createTextNode(clean));
    }
    el.scrollTop = el.scrollHeight;
  }

  function finaliseOutput(el) {
    // Ensure output area is visible.
    el.style.display = 'block';
  }

  // ---------------------------------------------------------------------------
  // Status badge (shared across all blocks on the page)
  // ---------------------------------------------------------------------------

  /** @type {HTMLElement|null} */
  let statusBadgeEl = null;

  function ensureStatusBadge() {
    if (statusBadgeEl) return statusBadgeEl;
    statusBadgeEl = document.createElement('span');
    statusBadgeEl.className = 'spack-run-status-badge spack-run-status-loading';
    statusBadgeEl.title = 'spack-lite is loading…';
    statusBadgeEl.textContent = '⏳ Loading spack-lite…';
    // Insert into a fixed position at bottom-right of page.
    statusBadgeEl.style.cssText = (
      'position:fixed;bottom:1rem;right:1rem;z-index:9999;'
      + 'padding:0.25rem 0.6rem;border-radius:999px;font-size:0.75rem;'
      + 'background:#fab387;color:#1e1e2e;font-family:monospace;'
      + 'pointer-events:none;'
    );
    document.body.appendChild(statusBadgeEl);
    return statusBadgeEl;
  }

  function updateStatusBadge(state, message) {
    const badge = ensureStatusBadge();
    const labels = {
      loading: '⏳ ' + (message || 'Loading…'),
      ready: '✅ spack-lite ready',
      error: '❌ ' + (message || 'Error'),
    };
    badge.textContent = labels[state] || message || state;
    badge.className = 'spack-run-status-badge spack-run-status-' + state;
    if (state === 'ready') {
      // Auto-hide after 3 s.
      setTimeout(() => { badge.style.opacity = '0'; badge.style.transition = 'opacity 1s'; }, 3000);
    }
  }

  // ---------------------------------------------------------------------------
  // Button state
  // ---------------------------------------------------------------------------

  function setButtonState(btn, state) {
    btn.disabled = (state === 'running');
    btn.dataset.state = state;
    if (state === 'running') {
      btn.textContent = '⏳ Running…';
    } else if (state === 'loading') {
      btn.textContent = '⏳ Loading…';
    } else {
      btn.textContent = '▶ Run';
    }
  }

  // ---------------------------------------------------------------------------
  // DOM injection — add "Run ▶" buttons to all runnable blocks
  // ---------------------------------------------------------------------------

  function decorateBlocks() {
    const wrappers = document.querySelectorAll('[data-spack-runnable="true"]');
    wrappers.forEach(function (wrapper) {
      // Commands are stored as a JSON array to survive HTML attribute normalisation.
      let commands;
      try {
        commands = JSON.parse(wrapper.dataset.spackCommands || '[]');
      } catch (_) {
        commands = [];
      }
      if (!commands.length) return;

      // --- Run button ---
      const btn = document.createElement('button');
      btn.className = 'spack-run-button';
      btn.textContent = '▶ Run';
      btn.type = 'button';
      btn.title = 'Run this block in the browser using spack-lite';

      // --- Output area ---
      const outputEl = document.createElement('pre');
      outputEl.className = 'spack-run-output';
      outputEl.style.display = 'none';

      // --- Clear link ---
      const clearLink = document.createElement('button');
      clearLink.className = 'spack-run-clear';
      clearLink.textContent = 'Clear';
      clearLink.type = 'button';
      clearLink.style.display = 'none';

      // --- Container for button row ---
      const btnRow = document.createElement('div');
      btnRow.className = 'spack-run-button-row';
      btnRow.appendChild(btn);
      btnRow.appendChild(clearLink);

      wrapper.appendChild(btnRow);
      wrapper.appendChild(outputEl);

      // Show/hide clear link with output visibility.
      function syncClear() {
        clearLink.style.display = outputEl.style.display === 'none' ? 'none' : '';
      }

      clearLink.addEventListener('click', function () {
        outputEl.style.display = 'none';
        clearOutput(outputEl);
        syncClear();
      });

      btn.addEventListener('click', function () {
        if (!worker) {
          // First click on any block — create the worker.
          initWorker();
          setButtonState(btn, 'loading');
          ensureStatusBadge();
        }
        outputEl.style.display = 'block';
        syncClear();
        enqueueCommand(commands.join('\n'), outputEl, btn);
      });
    });
  }

  // ---------------------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------------------

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', decorateBlocks);
  } else {
    decorateBlocks();
  }

}());
