"""
sphinx_spack_lite._directives
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Patches Sphinx's built-in ``code-block`` directive to recognise a
``:runnable:`` flag.  When set, the generated code block is wrapped in a
``spack_run_container`` node whose HTML visitor emits a ``<div>`` with
``data-spack-runnable`` and ``data-spack-commands`` attributes.  The JS
layer (``spack_run.js``) scans for those attributes to inject "Run ▶" buttons.

The commands string is built by stripping the ``$ `` prompt prefix from
every line that begins with ``$ ``; continuation lines (output, comments)
are ignored.  The result is a newline-joined list of bare commands.
"""

from __future__ import annotations

import html
import re
from typing import List

from docutils import nodes
from sphinx.directives.code import CodeBlock


_PROMPT_RE = re.compile(r"^\$ ")


def _extract_commands(source: str) -> str:
    """Return bare shell commands from a console block (prompts stripped).

    Only lines beginning with ``$ `` are treated as command lines; all
    other lines (output, blank lines, comments) are dropped.
    """
    commands: List[str] = []
    for line in source.splitlines():
        if _PROMPT_RE.match(line):
            commands.append(_PROMPT_RE.sub("", line, count=1))
    return "\n".join(commands)


# ---------------------------------------------------------------------------
# Custom wrapper node
# ---------------------------------------------------------------------------

class spack_run_container(nodes.General, nodes.Element):
    """Wrapper node that carries spack-lite execution metadata as HTML data attrs."""


def visit_spack_run_container_html(
    self,  # Sphinx HTMLTranslator
    node: spack_run_container,
) -> None:
    commands = node.get("data-spack-commands", "")
    escaped = html.escape(commands, quote=True)
    self.body.append(
        f'<div class="spack-run-wrapper"'
        f' data-spack-runnable="true"'
        f' data-spack-commands="{escaped}">\n'
    )


def depart_spack_run_container_html(
    self,  # Sphinx HTMLTranslator
    node: spack_run_container,
) -> None:
    self.body.append("</div>\n")


def _noop(self, node: spack_run_container) -> None:  # type: ignore[type-arg]
    """No-op visitor for non-HTML builders (LaTeX, text, …)."""


# ---------------------------------------------------------------------------
# Patched CodeBlock directive
# ---------------------------------------------------------------------------

class RunnableCodeBlock(CodeBlock):
    """``code-block`` subclass that adds the ``:runnable:`` boolean option."""

    option_spec = {**CodeBlock.option_spec, "runnable": lambda _: True}

    def run(self) -> list:  # type: ignore[override]
        result = super().run()
        if not self.options.get("runnable"):
            return result

        source_text = "\n".join(self.content)
        commands = _extract_commands(source_text)

        wrapper = spack_run_container()
        wrapper["data-spack-commands"] = commands
        wrapper += result
        return [wrapper]

