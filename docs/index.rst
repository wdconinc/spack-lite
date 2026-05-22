spack-lite Documentation
========================

.. image:: https://img.shields.io/github/actions/workflow/status/wdconinc/spack-lite/deploy.yml?branch=main
   :alt: Build status
   :target: https://github.com/wdconinc/spack-lite/actions/workflows/deploy.yml

**spack-lite** runs the `Spack package manager <https://spack.io>`_ entirely
inside a browser tab — no server, no installation.

It uses `Pyodide <https://pyodide.org/>`_ (WebAssembly Python) to execute Spack
Python code directly in the browser, with a custom POSIX-shell interpreter and
OS shim layer that bridges the gap between Spack's Linux assumptions and the
sandboxed browser environment.

.. grid:: 2
   :gutter: 2

   .. grid-item-card:: 🚀 Try it now
      :link: https://wdconinc.github.io/spack-lite/

      Open the live browser terminal — no install needed.

   .. grid-item-card:: 📖 GitHub repository
      :link: https://github.com/wdconinc/spack-lite

      Source code, issues, and pull requests.

.. admonition:: Interactive documentation

   The command blocks marked **▶ Run** on these pages execute inside your
   browser using spack-lite.  Click one to start — the Spack environment
   loads on demand and persists across all blocks on the same page.

Quick start
-----------

Once the environment is ready (takes ~20 s the first time while Pyodide,
clingo, and the Spack archive download) you can explore Spack interactively:

.. code-block:: console
   :runnable:

   $ spack help

List the packages in the seed set:

.. code-block:: console
   :runnable:

   $ spack list

.. toctree::
   :maxdepth: 2
   :caption: Contents

   design
   using_spack
   sphinx_extension
