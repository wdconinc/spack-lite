# Configuration file for the Sphinx documentation builder.
#
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os

# ---------------------------------------------------------------------------
# Project information
# ---------------------------------------------------------------------------

project = "spack-lite"
copyright = "2025, spack-lite contributors"
author = "spack-lite contributors"
release = "0.1"

# ---------------------------------------------------------------------------
# Extensions
# ---------------------------------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx_design",
    "sphinx_spack_lite",
    "sphinx_copybutton",
]

# ---------------------------------------------------------------------------
# spack-lite interactive extension
# ---------------------------------------------------------------------------
# Assets (worker.js, spack-lite.tar.gz, spack-packages.tar.gz) are served
# from the GitHub Pages root, one level above this docs sub-directory.
# We use an absolute URL so it works regardless of where the page is served.
spack_lite_base_url = os.environ.get(
    "SPACK_LITE_BASE_URL",
    "https://wdconinc.github.io/spack-lite/",
)

# ---------------------------------------------------------------------------
# General
# ---------------------------------------------------------------------------

exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# ---------------------------------------------------------------------------
# HTML output (Furo theme — same as Spack RTD docs)
# ---------------------------------------------------------------------------

html_theme = "furo"

html_title = "spack-lite"

html_theme_options = {
    "source_repository": "https://github.com/wdconinc/spack-lite",
    "source_branch": "main",
    "source_directory": "docs/",
    "navigation_with_keys": True,
    "footer_icons": [
        {
            "name": "GitHub",
            "url": "https://github.com/wdconinc/spack-lite",
            "html": """
                <svg stroke="currentColor" fill="currentColor" stroke-width="0"
                     viewBox="0 0 16 16" height="1em" width="1em"
                     xmlns="http://www.w3.org/2000/svg">
                  <path fill-rule="evenodd"
                    d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38
                       0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13
                       -.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66
                       .07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15
                       -.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27
                       .68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12
                       .51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48
                       0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8
                       c0-4.42-3.58-8-8-8z"/>
                </svg>
            """,
            "class": "",
        },
    ],
}

html_static_path = ["_static"]

html_css_files = ["custom.css"]
