"""
sphinx_spack_lite._directives
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Patches Sphinx's built-in ``code-block`` directive to recognise a
``:runnable:`` flag.  When set, the generated code block is wrapped in a
``spack_run_container`` node whose HTML visitor emits a ``<div>`` with
``data-spack-runnable`` and ``data-spack-commands`` attributes.  The JS
layer (``spack_run.js``) scans for those attributes to inject "Run" buttons.

The commands string is built by stripping the shell prompt prefix
(lines starting with ``$``) from every line that begins with it;
continuation lines (output, comments)
are ignored.  The result is a newline-joined list of bare commands.
"""

from __future__ import annotations

import html
import json
import re
from typing import List

from docutils import nodes
from sphinx.directives.code import CodeBlock


_PROMPT_RE = re.compile(r"^\$ ")


def _extract_commands(source: str) -> str:
    """Return a JSON array of bare shell commands from a console block.

    Only lines beginning with a shell prompt (``$``) are treated as command
    lines; all other lines (output, blank lines, comments) are dropped.

    The result is a JSON-encoded list so that it survives round-trip through
    an HTML attribute value (HTML parsers normalise raw newlines in attributes
    to spaces, which would corrupt a newline-joined string).
    """
    commands: List[str] = []
    for line in source.splitlines():
        if _PROMPT_RE.match(line):
            commands.append(_PROMPT_RE.sub("", line, count=1))
    return json.dumps(commands)


# ---------------------------------------------------------------------------
# Custom wrapper node
# ---------------------------------------------------------------------------

class spack_run_container(nodes.General, nodes.Element):
    """Wrapper node that carries spack-lite execution metadata as HTML data attrs."""


def visit_spack_run_container_html(
    self,  # Sphinx HTMLTranslator
    node: spack_run_container,
) -> None:
    # node["data-spack-commands"] is already a JSON string; escaping it for
    # an HTML attribute is safe because json.dumps never emits bare `<`/`>`.
    commands_json = node.get("data-spack-commands", "[]")
    escaped = html.escape(commands_json, quote=True)
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

