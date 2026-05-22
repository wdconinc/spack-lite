"""Tests for the sphinx_spack_lite extension."""

from __future__ import annotations

import json
import textwrap

import pytest

from sphinx_spack_lite._directives import _extract_commands


# ---------------------------------------------------------------------------
# _extract_commands unit tests
# ---------------------------------------------------------------------------

def test_extract_commands_single():
    source = "$ spack list"
    result = _extract_commands(source)
    assert json.loads(result) == ["spack list"]


def test_extract_commands_multi_line():
    source = textwrap.dedent("""\
        $ spack list
        $ spack info hdf5
    """)
    result = _extract_commands(source)
    assert json.loads(result) == ["spack list", "spack info hdf5"]


def test_extract_commands_strips_prompt():
    source = "$ spack spec zlib"
    result = _extract_commands(source)
    assert json.loads(result) == ["spack spec zlib"]


def test_extract_commands_ignores_output_lines():
    source = textwrap.dedent("""\
        $ spack list
        ==> 100 packages
        zlib
        $ spack info zlib
    """)
    result = _extract_commands(source)
    assert json.loads(result) == ["spack list", "spack info zlib"]


def test_extract_commands_no_prompts():
    source = "spack list\nspack info hdf5"
    result = _extract_commands(source)
    assert json.loads(result) == []


def test_extract_commands_empty():
    assert json.loads(_extract_commands("")) == []


def test_extract_commands_returns_json_string():
    """Result must be valid JSON (survives HTML attribute round-trip)."""
    source = "$ spack list\n$ spack info hdf5"
    result = _extract_commands(source)
    parsed = json.loads(result)
    assert isinstance(parsed, list)
    assert all(isinstance(cmd, str) for cmd in parsed)


def test_extract_commands_newlines_safe_in_json():
    """Commands with spaces/special chars are safely encoded in JSON."""
    source = '$ spack install hdf5 +fortran ~mpi'
    result = _extract_commands(source)
    parsed = json.loads(result)
    assert parsed == ["spack install hdf5 +fortran ~mpi"]


# ---------------------------------------------------------------------------
# Minimal Sphinx build test
# ---------------------------------------------------------------------------

def test_sphinx_build_runnable_block(tmp_path):
    """A :runnable: code-block should produce a spack_run_container wrapper."""
    pytest.importorskip("sphinx")
    from sphinx.application import Sphinx

    srcdir = tmp_path / "src"
    srcdir.mkdir()
    outdir = tmp_path / "out"
    outdir.mkdir()
    doctreedir = tmp_path / "doctrees"
    doctreedir.mkdir()

    (srcdir / "conf.py").write_text(
        textwrap.dedent("""\
            extensions = ["sphinx_spack_lite"]
            master_doc = "index"
            exclude_patterns = []
        """)
    )
    (srcdir / "index.rst").write_text(
        textwrap.dedent("""\
            Test
            ====

            .. code-block:: console
               :runnable:

               $ spack list
        """)
    )

    app = Sphinx(
        srcdir=str(srcdir),
        confdir=str(srcdir),
        outdir=str(outdir),
        doctreedir=str(doctreedir),
        buildername="html",
        verbosity=0,
    )
    app.build()

    html = (outdir / "index.html").read_text()
    assert 'data-spack-runnable="true"' in html
    assert "data-spack-commands" in html

    # Commands attribute must be valid JSON containing "spack list".
    import re
    m = re.search(r'data-spack-commands="([^"]*)"', html)
    assert m is not None
    import html as html_mod
    commands = json.loads(html_mod.unescape(m.group(1)))
    assert commands == ["spack list"]
