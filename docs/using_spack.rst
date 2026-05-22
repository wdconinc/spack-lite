Using Spack in Your Browser
===========================

This page lets you explore Spack interactively without installing anything.
Click **▶ Run** on any block below to start — the spack-lite environment
loads on demand in your browser and the same Spack session persists across
all blocks on this page.

.. admonition:: First run takes ~20 seconds
   :class: tip

   On the first click, Pyodide (WebAssembly Python), clingo (the ASP solver),
   and the Spack archive download in the background.  Subsequent clicks are
   instant because the environment stays loaded for the whole page visit.

Getting help
------------

Start by asking Spack what it can do:

.. code-block:: console
   :runnable:

   $ spack help

Finding packages
----------------

List all packages in the seed set (full list loads after a few seconds):

.. code-block:: console
   :runnable:

   $ spack list

Filter by keyword — try ``python``:

.. code-block:: console
   :runnable:

   $ spack list python

Package information
-------------------

Inspect a specific package.  ``hdf5`` is a common HPC I/O library:

.. code-block:: console
   :runnable:

   $ spack info hdf5

``zlib`` is a foundational compression library present in the seed set:

.. code-block:: console
   :runnable:

   $ spack info zlib

Concretising a spec
-------------------

``spack spec`` resolves a package specification into a fully concrete dependency
tree (versions, variants, compilers).  This uses the clingo ASP solver and may
take 5–15 s for the first run:

.. code-block:: console
   :runnable:

   $ spack spec zlib

Try a build variant — HDF5 with Fortran support disabled:

.. code-block:: console
   :runnable:

   $ spack spec hdf5~fortran

Working with the virtual filesystem
------------------------------------

The Spack tree is unpacked into an in-memory filesystem.  You can explore it
with the built-in shell commands:

.. code-block:: console
   :runnable:

   $ ls /home/pyodide/spack
   $ ls /home/pyodide/spack/var/spack/repos/builtin/packages | head -20

Inspect a package recipe directly:

.. code-block:: console
   :runnable:

   $ cat /home/pyodide/spack/var/spack/repos/builtin/packages/zlib/package.py

Environment variables
---------------------

.. code-block:: console
   :runnable:

   $ env | grep SPACK

Spack configuration
-------------------

See the fake compiler and package configuration that spack-lite injects so
the concretiser has a host environment to target:

.. code-block:: console
   :runnable:

   $ cat /home/pyodide/.spack/linux/compilers.yaml

.. code-block:: console
   :runnable:

   $ cat /home/pyodide/.spack/packages.yaml

Loading the full package set
-----------------------------

By default only the seed packages are available (fast startup).  Run
``spack load-packages`` to load all ~8 000 Spack packages in the background:

.. code-block:: console
   :runnable:

   $ spack load-packages

After loading, try listing a package that isn't in the seed set:

.. code-block:: console
   :runnable:

   $ spack list mpich
