"""
sphinx_spack_lite
~~~~~~~~~~~~~~~~~

A Sphinx extension that integrates spack-lite (the browser-based Spack REPL)
into Sphinx documentation.

Usage
-----

In ``conf.py``::

    extensions = [
        ...,
        "sphinx_spack_lite",
    ]

    # URL prefix where worker.js, shim_system.py, shell.py,
    # spack-lite.tar.gz and spack-packages.tar.gz are served.
    # Default is "_static/" (i.e., they are bundled into the docs build).
    # For ReadTheDocs you may want to point this at a GitHub Release URL
    # to avoid bloating the docs build with large binary tarballs.
    spack_lite_base_url = "https://github.com/your-org/spack-lite/releases/latest/download/"

In ``getting_started.rst``::

    .. code-block:: console
       :runnable:

       $ spack list

Notes
-----

- The Web Worker (``worker.js``) is initialised *lazily*: nothing loads
  until the user clicks the first "Run ▶" button on a page.

- The worker is shared across all runnable blocks on a page, so the Spack
  environment (installed packages, cwd, etc.) persists between block runs.

- ``SharedArrayBuffer``-based interrupt (Ctrl-C) is unavailable on
  ReadTheDocs because it requires COOP/COEP HTTP headers that RTD does not
  support.  Basic command execution is unaffected.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from docutils.parsers.rst import directives
from sphinx.application import Sphinx

from ._directives import (
    RunnableCodeBlock,
    depart_spack_run_container_html,
    spack_run_container,
    visit_spack_run_container_html,
    _noop,
)


# ---------------------------------------------------------------------------
# Extension metadata
# ---------------------------------------------------------------------------

__version__ = "0.1.0"


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

def _builder_inited(app: Sphinx) -> None:
    """Copy static assets from the extension's own static/ directory."""
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    app.config.html_static_path = list(app.config.html_static_path) + [static_dir]


def _html_page_context(
    app: Sphinx,
    pagename: str,
    templatename: str,
    context: Dict[str, Any],
    doctree: Any,
) -> None:
    """Inject JS/CSS and the SPACK_LITE_CONFIG global on pages with runnable blocks."""
    if doctree is None:
        return

    # Only inject assets on pages that actually contain runnable blocks.
    if not doctree.traverse(spack_run_container):
        return

    base_url = getattr(app.config, "spack_lite_base_url", "") or "_static/"
    # Ensure trailing slash
    if not base_url.endswith("/"):
        base_url += "/"

    worker_url = f"{base_url}worker.js"
    spack_lite_url = f"{base_url}spack-lite.tar.gz"
    spack_packages_url = f"{base_url}spack-packages.tar.gz"

    # Inject an inline <script> that sets the config before spack_run.js loads.
    config_script = (
        "<script>\n"
        "window.SPACK_LITE_CONFIG = {\n"
        f'  workerUrl: "{worker_url}",\n'
        f'  spackLiteUrl: "{spack_lite_url}",\n'
        f'  spackPackagesUrl: "{spack_packages_url}"\n'
        "};\n"
        "</script>"
    )

    # Sphinx accumulates scripts via app.add_js_file(); for an inline block we
    # attach it to the metatags list which Furo (and most other themes) renders
    # in the <head> via {{ metatags }}.  As a fallback we also include it in
    # script_files so it always ends up in the page.
    context.setdefault("metatags", "")
    context["metatags"] += config_script


# ---------------------------------------------------------------------------
# Extension setup
# ---------------------------------------------------------------------------

def setup(app: Sphinx) -> Dict[str, Any]:
    # Config value: base URL for spack-lite assets.
    # Empty string means "use _static/" (assets bundled into docs build).
    app.add_config_value("spack_lite_base_url", default="", rebuild="html")

    # Register the custom node and its HTML visitors.
    app.add_node(
        spack_run_container,
        html=(visit_spack_run_container_html, depart_spack_run_container_html),
        latex=(_noop, _noop),
        text=(_noop, _noop),
        man=(_noop, _noop),
        texinfo=(_noop, _noop),
    )

    # Override the built-in code-block directive.
    app.add_directive("code-block", RunnableCodeBlock, override=True)
    # Sphinx also registers `code-block` under the alias `sourcecode`.
    app.add_directive("sourcecode", RunnableCodeBlock, override=True)

    # Static assets (spack_run.js + spack_run.css) are added from our own
    # static/ directory via the builder-inited event (see _builder_inited).
    app.connect("builder-inited", _builder_inited)
    app.add_js_file("spack_run.js", loading_method="defer")
    app.add_css_file("spack_run.css")

    # Inject the per-page SPACK_LITE_CONFIG <script> block.
    app.connect("html-page-context", _html_page_context)

    return {
        "version": __version__,
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }
