sphinx_spack_lite Extension
===========================

The ``sphinx_spack_lite`` Python package is a Sphinx extension that brings the
spack-lite interactive terminal to any Sphinx documentation site.  It adds a
``:runnable:`` option to the standard ``code-block`` directive; marked blocks
gain a **▶ Run** button that lazily initialises the spack-lite Web Worker and
streams output inline — with zero impact on page-load time for readers who do
not click.

Installation
------------

The extension lives in the `spack-lite repository
<https://github.com/wdconinc/spack-lite>`_ and can be installed directly from
GitHub:

.. code-block:: console

   $ pip install git+https://github.com/wdconinc/spack-lite.git

Or, if you have cloned the repository locally:

.. code-block:: console

   $ pip install -e /path/to/spack-lite

Configuration
-------------

Add ``"sphinx_spack_lite"`` to your ``conf.py`` extension list and set
``spack_lite_base_url`` to the URL prefix where ``worker.js``,
``spack-lite.tar.gz``, and ``spack-packages.tar.gz`` are served:

.. code-block:: python

   # conf.py
   extensions = [
       ...,
       "sphinx_spack_lite",
   ]

   # URL prefix where spack-lite assets are hosted.
   # Must end with '/'.  Defaults to "_static/" (bundle into docs build).
   # For ReadTheDocs, point to a GitHub Releases URL to avoid bloating
   # the docs build with large binary tarballs.
   spack_lite_base_url = "https://wdconinc.github.io/spack-lite/"

Authoring runnable blocks
--------------------------

Add ``:runnable:`` to any ``code-block`` directive whose shell commands should
be executable in the browser.  The extension extracts every line that begins
with a ``$`` prompt, strips it, and passes the joined commands to the worker
when the reader clicks **▶ Run**:

.. code-block:: rst

   .. code-block:: console
      :runnable:

      $ spack list

      $ spack info hdf5

Multiple ``$ ...`` lines are concatenated and run as a single logical session.
Non-prompt lines (output examples, comments) are ignored by the runner but
still rendered for the reader.

How it works
------------

1. **Build time** — ``RunnableCodeBlock`` wraps the highlighted code block in
   a ``<div data-spack-runnable="true" data-spack-commands="…">`` container.
   The commands string is the prompt-stripped text embedded as an HTML
   attribute.

2. **Page load** — ``spack_run.js`` is added to every page that contains at
   least one runnable block (via Sphinx's ``app.add_js_file`` with
   ``loading_method="defer"``).  On ``DOMContentLoaded`` it scans for
   ``[data-spack-runnable]`` elements and injects **▶ Run** buttons and
   output ``<pre>`` elements — but does *not* start the worker.

3. **First click** — the worker is created with ``new Worker(workerUrl)``.
   A ``{ type: 'configure', … }`` message is posted immediately so the worker
   uses the correct asset URLs.  A loading indicator appears on the button.

4. **Execution** — subsequent clicks (or the first click once the worker is
   ready) post ``{ type: 'run', command }`` to the worker.  ``stdout`` chunks
   stream into the inline output area in real time; a ``result`` message
   marks the block as done.

5. **Shared environment** — the same worker handles all blocks on the page, so
   a ``cd`` or ``spack install`` in one block affects subsequent blocks.

ReadTheDocs notes
-----------------

ReadTheDocs does not currently support setting custom
``Cross-Origin-Opener-Policy`` / ``Cross-Origin-Embedder-Policy`` HTTP headers,
which are required to enable ``SharedArrayBuffer``.  Pyodide can run without
``SharedArrayBuffer`` — only the Ctrl-C interrupt feature is degraded.  Basic
command execution is unaffected.

For ``spack_lite_base_url``, point to a GitHub Releases download URL rather
than bundling the binary tarballs into the docs build:

.. code-block:: python

   spack_lite_base_url = (
       "https://github.com/wdconinc/spack-lite/releases/latest/download/"
   )

You will need to create a GitHub Release containing ``spack-lite.tar.gz`` and
``spack-packages.tar.gz`` as release assets.

Extension API reference
-----------------------

.. automodule:: sphinx_spack_lite
   :members:

.. automodule:: sphinx_spack_lite._directives
   :members: RunnableCodeBlock, spack_run_container, _extract_commands
