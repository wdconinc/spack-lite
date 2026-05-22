Design & Architecture
=====================

spack-lite runs the `Spack <https://spack.io>`_ package manager in a browser
tab with zero server-side infrastructure.  This page describes how the pieces
fit together.

Overview
--------

.. code-block:: text

   Browser tab
   ├── index.html  (xterm.js terminal UI)
   │    └── Web Worker  (worker.js)
   │         ├── Pyodide  (WebAssembly CPython 3.12)
   │         │    ├── clingo  (ASP solver for Spack concretisation)
   │         │    ├── shim_system.py  (OS / stdlib monkey-patches)
   │         │    └── shell.py  (POSIX-like shell + spack command router)
   │         └── wasm-git  (libgit2 compiled to WASM — optional)
   └── Fetched lazily
        ├── spack-lite.tar.gz   (~10 MB — stripped Spack + seed packages)
        └── spack-packages.tar.gz  (full package repo — loaded on demand)

The browser's **main thread** runs the xterm.js terminal UI and communicates
with the worker via ``postMessage``.  No shared memory is required; the worker
is entirely self-contained.

Components
----------

Pyodide
~~~~~~~

`Pyodide <https://pyodide.org/>`_ compiles CPython to WebAssembly and provides
a Python standard library that runs in the browser.  spack-lite pins Pyodide
**0.27.3** which bundles Python 3.12 and a pre-built ``clingo`` wheel — the
exact version of the ASP solver that Spack's concretiser requires.

The Pyodide runtime is loaded from the jsDelivr CDN:

.. code-block:: text

   https://cdn.jsdelivr.net/pyodide/v0.27.3/full/pyodide.js

System shim (``shim_system.py``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Spack uses many OS-level calls that don't exist in the browser sandbox:
``fork``, ``execve``, ``ioctl``, ``termios``, and more.  ``shim_system.py``
monkey-patches the relevant Python standard library modules so that Spack's
Python layer runs unmodified:

* ``subprocess.run`` / ``Popen`` — re-implemented using ``ProcessPoolExecutor``
  with a resilient fallback to serial execution
* ``os.fork``, ``os.execve`` — raise ``NotImplementedError`` with a helpful message
* ``termios``, ``tty``, ``readline``, ``fcntl``, ``grp``, ``pwd`` — replaced with
  lightweight stubs that satisfy Spack's import-time checks

Shell interpreter (``shell.py``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``shell.py`` implements a minimal POSIX-like shell inside Python.  It parses a
line of shell input, expands environment variables, and dispatches to either:

* **Built-in commands** — ``cd``, ``ls``, ``cat``, ``grep``, ``echo``, ``pwd``,
  ``head``, ``tail``, ``env``, ``which``, …
* **spack** — routes to ``spack.main.main()`` directly (no subprocess)
* **Python scripts** — executed via ``exec()`` inside the Pyodide runtime

Web Worker message protocol
----------------------------

The main thread and the worker communicate with a simple message protocol:

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Direction
     - ``type``
     - Payload
   * - main → worker
     - ``configure``
     - ``{ spackLiteUrl, spackPackagesUrl }`` — override asset URLs
   * - main → worker
     - ``run``
     - ``{ command }`` — shell command string to execute
   * - main → worker
     - ``load-packages``
     - *(none)* — trigger background loading of full package archive
   * - worker → main
     - ``status``
     - ``{ state, message }`` — init progress (``loading`` / ``ready`` / ``error``)
   * - worker → main
     - ``stdout``
     - ``{ text }`` — streamed output chunk
   * - worker → main
     - ``result``
     - ``{ output, cwd }`` — command finished; final merged output + new cwd
   * - worker → main
     - ``error``
     - ``{ message }`` — command or worker error

Startup sequence
-----------------

The worker initialises itself automatically on creation:

1. Load ``pyodide.js`` from jsDelivr CDN
2. Load ``wasm-git`` (libgit2 WASM) for optional ``git`` support
3. Redirect Python ``sys.stdout`` / ``sys.stderr`` to the terminal
4. Install ``clingo`` via ``pyodide.loadPackage('clingo')``
5. Fetch and unpack ``spack-lite.tar.gz`` into ``/home/pyodide/spack``
6. Add Spack to ``sys.path``; set ``SPACK_ROOT`` / ``HOME`` / cwd
7. Write fake compiler + package config into ``~/.spack``
8. Execute ``shim_system.py`` to monkey-patch the stdlib
9. Execute ``shell.py`` to install the POSIX shell interpreter
10. Post ``{ type: 'status', state: 'ready' }``

Total cold-start time is typically **15–25 s** on a modern desktop, dominated
by the Pyodide WASM download (~8 MB) and the spack-lite archive (~10 MB).

spack-lite archive
------------------

``spack-lite.tar.gz`` is a stripped-down copy of the Spack source tree plus a
curated seed set of package recipes sufficient for the concretiser to resolve
common specifications.  It is built by ``scripts/make_spack_lite.sh``:

1. Clone Spack ``develop``
2. Remove all package recipes
3. Re-add the transitive closure of a curated ``KEEP_PKGS`` seed list
4. Strip tests, docs, and large data files
5. Pack as ``gztar``

The companion ``spack-packages.tar.gz`` contains the full package repository
(~8 000 packages) and is loaded lazily in the background after the REPL is
already active, unlocking ``spack list`` / ``spack info`` for all packages.

Package availability
--------------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Archive
     - What it unlocks
   * - ``spack-lite.tar.gz`` (always loaded)
     - Seed packages: ``zlib``, ``hdf5``, ``python``, ``cmake``, and ~50 others
   * - ``spack-packages.tar.gz`` (loaded on demand)
     - All ~8 000 Spack packages

Run ``spack load-packages`` (or just wait ~30 s) to load the full set:

.. code-block:: console
   :runnable:

   $ spack load-packages

Sphinx extension (``sphinx_spack_lite``)
-----------------------------------------

The ``sphinx_spack_lite`` Sphinx extension embeds the spack-lite experience
into any Sphinx documentation site.  See :doc:`sphinx_extension` for full
details.
