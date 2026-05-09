"""
test_shell.py — tests for shell.py built-in commands.

Each test invokes the command through pyodide_runner.py so that the
shim_system.py monkey-patches are active during execution, exactly
mirroring the browser's Pyodide environment.
"""

import importlib.util
import os

import pytest
from helpers import run_in_shell

# ---------------------------------------------------------------------------
# Load _spack_python_is_interactive from shell.py without running the whole
# module (which would exec shims and require spack to be installed).
# ---------------------------------------------------------------------------
_SHELL_PY = os.path.join(os.path.dirname(os.path.dirname(__file__)), "shell.py")
_spec = importlib.util.spec_from_file_location("_shell_funcs", _SHELL_PY)
_shell_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shell_mod)
_spack_python_is_interactive = _shell_mod._spack_python_is_interactive


class TestSpackPythonIsInteractive:
    """Unit tests for _spack_python_is_interactive helper."""

    def test_empty_args_is_interactive(self):
        assert _spack_python_is_interactive([]) is True

    def test_dash_i_alone_is_interactive(self):
        assert _spack_python_is_interactive(['-i']) is True

    def test_dash_c_code_not_interactive(self):
        assert _spack_python_is_interactive(['-c', 'print(1)']) is False

    def test_dash_c_combined_not_interactive(self):
        assert _spack_python_is_interactive(['-cprint(1)']) is False

    def test_script_arg_not_interactive(self):
        assert _spack_python_is_interactive(['script.py']) is False

    def test_dash_i_with_script_is_interactive(self):
        # -i script.py: -i forces interactive mode after running the script
        assert _spack_python_is_interactive(['-i', 'script.py']) is True

    def test_dash_i_with_dash_c_is_interactive(self):
        # -i -c "code": -i forces interactive mode after executing the code
        assert _spack_python_is_interactive(['-i', '-c', 'print(1)']) is True

    def test_double_dash_separator_not_interactive(self):
        # -- terminates flag processing; anything after is positional
        assert _spack_python_is_interactive(['--', 'script.py']) is False


class TestEcho:
    def test_basic(self):
        r = run_in_shell("echo hello world")
        assert r.returncode == 0
        assert r.stdout.strip() == "hello world"

    def test_empty(self):
        r = run_in_shell("echo")
        assert r.returncode == 0
        assert r.stdout == "\n"

    def test_special_chars(self):
        r = run_in_shell("echo foo-bar_baz")
        assert r.returncode == 0
        assert "foo-bar_baz" in r.stdout


class TestPwd:
    def test_returns_path(self):
        r = run_in_shell("pwd")
        assert r.returncode == 0
        assert r.stdout.strip().startswith("/")


class TestCd:
    def test_cd_to_tmp(self):
        r = run_in_shell("cd /tmp")
        assert r.returncode == 0
        assert r.stdout == ""  # cd produces no output on success

    def test_cd_nonexistent(self):
        r = run_in_shell("cd /nonexistent_path_xyz")
        assert r.returncode == 0  # shell returns 0; error is in output
        assert "No such file or directory" in r.stdout

    def test_cd_tilde(self):
        r = run_in_shell("cd ~")
        assert r.returncode == 0
        assert r.stdout == ""  # cd produces no output on success


class TestLs:
    def test_root(self):
        r = run_in_shell("ls /")
        assert r.returncode == 0
        assert r.stdout.strip()

    def test_tmp(self):
        r = run_in_shell("ls /tmp")
        assert r.returncode == 0  # may be empty but should not error

    def test_nonexistent(self):
        r = run_in_shell("ls /nonexistent_xyz")
        assert r.returncode == 0
        assert "No such file or directory" in r.stdout

    def test_long_format(self):
        r = run_in_shell("ls -l /")
        assert r.returncode == 0
        # Long format includes a mode character and size field
        assert any(line.startswith(("d", "-")) for line in r.stdout.splitlines())


class TestMkdirRmCpMv:
    def test_mkdir_rm(self):
        run_in_shell("mkdir -p /tmp/spack_lite_test_dir")
        r = run_in_shell("ls /tmp | grep spack_lite_test_dir")
        assert r.returncode == 0
        assert "spack_lite_test_dir" in r.stdout
        run_in_shell("rm -rf /tmp/spack_lite_test_dir")

    def test_mkdir_existing_no_p(self):
        # Create a fresh directory, then try to create it again without -p.
        run_in_shell("mkdir -p /tmp/spack_lite_existing_test")
        r = run_in_shell("mkdir /tmp/spack_lite_existing_test")
        assert r.returncode == 0
        assert "File exists" in r.stdout
        run_in_shell("rm -rf /tmp/spack_lite_existing_test")

    def test_cp_file(self):
        # Create source file via Python (the shell has no I/O redirection)
        src = "/tmp/spack_lite_src.txt"
        dst = "/tmp/spack_lite_dst.txt"
        try:
            with open(src, "w") as fh:
                fh.write("hello from cp test\n")
            r_cp = run_in_shell(f"cp {src} {dst}")
            assert r_cp.returncode == 0
            assert r_cp.stdout == ""  # cp produces no output on success
            r_cat = run_in_shell(f"cat {dst}")
            assert "hello from cp test" in r_cat.stdout
        finally:
            for path in (src, dst):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass

    def test_mv_file(self):
        run_in_shell("mkdir -p /tmp/spack_lite_mv_test")
        r = run_in_shell("mv /tmp/spack_lite_mv_test /tmp/spack_lite_mv_done")
        assert r.returncode == 0
        assert r.stdout == ""  # mv produces no output on success
        r_ls = run_in_shell("ls /tmp | grep spack_lite_mv_done")
        assert "spack_lite_mv_done" in r_ls.stdout
        run_in_shell("rm -rf /tmp/spack_lite_mv_done")


class TestCat:
    def test_missing_file(self):
        r = run_in_shell("cat /nonexistent_xyz.txt")
        assert r.returncode == 0
        assert "No such file or directory" in r.stdout

    def test_reads_real_file(self, tmp_path):
        test_file = tmp_path / "test.py"
        test_file.write_text("# package.py content\nprint('hello')\n")
        r = run_in_shell(f"cat {test_file}")
        assert r.returncode == 0
        assert "# package.py content" in r.stdout

    def test_reads_real_file_with_lzma_unavailable(self, tmp_path):
        """Regression: cat must not raise LZMAError when lzma is unavailable.

        In Pyodide, lzma is unvendored from the stdlib.  The lzma shim in
        shim_system.py previously defined ``open`` in the exec namespace,
        shadowing the built-in open and causing _cmd_cat to raise LZMAError
        instead of reading the file.
        """
        test_file = tmp_path / "package.py"
        test_file.write_text("# zlib package\nversion('1.3')\n")
        r = run_in_shell(
            f"cat {test_file}",
            extra_env={"SPACK_LITE_MOCK_LZMA_UNAVAILABLE": "1"},
        )
        assert r.returncode == 0
        assert "# zlib package" in r.stdout
        assert "lzma" not in r.stdout.lower()


class TestGrep:
    def test_pipe_match(self):
        r = run_in_shell("echo hello world | grep hello")
        assert r.returncode == 0
        assert "hello" in r.stdout

    def test_pipe_no_match(self):
        r = run_in_shell("echo hello world | grep zzz_no_match")
        assert r.returncode == 0
        assert r.stdout == ""

    def test_invert(self):
        r = run_in_shell("echo hello world | grep -v hello")
        assert r.returncode == 0
        assert r.stdout == ""

    def test_case_insensitive(self):
        r = run_in_shell("echo HELLO | grep -i hello")
        assert r.returncode == 0
        assert "HELLO" in r.stdout

    def test_missing_pattern(self):
        r = run_in_shell("echo foo | grep")
        assert r.returncode == 0
        assert "missing pattern" in r.stdout


class TestHeadTail:
    def test_head_default(self):
        # Generate 15 lines via pipeline and take the first 10
        words = " ".join(str(i) for i in range(15))
        r = run_in_shell(f"echo {words} | head")
        assert r.returncode == 0

    def test_tail_n(self):
        r = run_in_shell("echo foo | tail -n 1")
        assert r.returncode == 0
        assert "foo" in r.stdout

    def test_head_n(self):
        r = run_in_shell("echo bar | head -n 1")
        assert r.returncode == 0
        assert "bar" in r.stdout


class TestEnv:
    def test_shows_vars(self):
        r = run_in_shell("env")
        assert r.returncode == 0
        assert "=" in r.stdout
        # The shim sets these defaults
        assert any(line.startswith("HOME=") for line in r.stdout.splitlines())
        assert "EDITOR=vi" in r.stdout
        assert "VISUAL=vi" in r.stdout


class TestWhich:
    def test_builtin(self):
        r = run_in_shell("which echo")
        assert r.returncode == 0
        assert "echo" in r.stdout
        assert "built-in" in r.stdout

    def test_missing(self):
        r = run_in_shell("which zzz_no_such_cmd")
        assert r.returncode == 0
        assert "not found" in r.stdout

    def test_editor_available(self):
        r = run_in_shell("which vi")
        assert r.returncode == 0
        assert "/usr/bin/vi" in r.stdout


class TestFind:
    def test_find_tmp(self):
        r = run_in_shell("find /tmp -maxdepth 1 -type d")
        assert r.returncode == 0

    def test_find_name_filter(self):
        run_in_shell("mkdir -p /tmp/spack_find_test")
        r = run_in_shell("find /tmp -name spack_find_test -type d")
        run_in_shell("rm -rf /tmp/spack_find_test")
        assert r.returncode == 0
        assert "spack_find_test" in r.stdout


class TestPipeline:
    def test_two_stage(self):
        r = run_in_shell("echo alpha beta gamma | grep alpha")
        assert r.returncode == 0
        assert "alpha" in r.stdout

    def test_three_stage(self):
        r = run_in_shell("echo foo bar baz | grep foo | grep foo")
        assert r.returncode == 0
        assert "foo" in r.stdout

    def test_unknown_command(self):
        r = run_in_shell("zzz_unknown_cmd arg")
        assert r.returncode == 0
        assert "command not found" in r.stdout


class TestVariableExpansion:
    def test_path_var(self):
        r = run_in_shell("echo $HOME")
        assert r.returncode == 0
        assert r.stdout.strip()  # should expand to something

    def test_undefined_var(self):
        r = run_in_shell("echo $SPACK_LITE_UNDEFINED_VAR_XYZ")
        assert r.returncode == 0
        assert r.stdout == "\n"  # expands to empty string


_SPACK_PYTHON_INTERACTIVE_MSG = "Interactive Python is not supported in browser mode."


class TestSpackSpec:
    """Regression test for 'spack spec zlib' — the user-visible failing command."""

    def test_spack_spec_zlib(self, spack_root):
        """``spack spec zlib`` must complete without [Errno 52] and print a concrete spec."""
        r = run_in_shell("spack spec zlib", timeout=120)
        assert r.returncode == 0, (
            "spack spec zlib failed.\n"
            f"stdout: {r.stdout}\n"
            f"stderr: {r.stderr}"
        )
        assert "zlib" in r.stdout, "Expected 'zlib' in spec output"
        assert "[Errno 52]" not in r.stdout
        assert "[Errno 52]" not in r.stderr

    def test_spack_spec_zlib_twice_no_oom(self, spack_root):
        """Running ``spack spec zlib`` twice must succeed both times.

        Regression for the OOM bug: Spack module-level caches and un-collected
        circular garbage from the first run must not cause the second run to
        exhaust memory or raise an error.  The gc.collect() + cache-reset in
        _cmd_spack's finally-block is what makes this reliable.
        """
        for i in range(2):
            r = run_in_shell("spack spec zlib", timeout=120)
            assert r.returncode == 0, (
                f"spack spec zlib failed on invocation {i + 1}.\n"
                f"stdout: {r.stdout}\n"
                f"stderr: {r.stderr}"
            )
            assert "zlib" in r.stdout, f"Expected 'zlib' in spec output (invocation {i + 1})"
            assert "Error" not in r.stdout or "zlib" in r.stdout, (
                f"Unexpected error in output (invocation {i + 1}): {r.stdout}"
            )

    def test_spack_load_packages_not_available_outside_browser(self, spack_root):
        """``spack load-packages`` outside the browser should show a graceful message."""
        r = run_in_shell("spack load-packages", timeout=30)
        assert r.returncode == 0
        # Outside the browser js module is absent, so we expect the fallback message.
        assert "not available" in r.stdout or "Loading" in r.stdout


class TestSpackPython:
    """Tests for 'spack python' interactive-mode detection in _cmd_spack."""

    def test_interactive_no_args_shows_helpful_message(self, spack_root):
        """'spack python' with no args should not crash with ESPIPE."""
        r = run_in_shell("spack python", extra_env={"SPACK_ROOT": spack_root})
        assert r.returncode == 0
        # Must NOT raise [Errno 29] I/O error
        assert "Errno 29" not in r.stdout
        assert "I/O error" not in r.stdout
        # Should tell the user how to use non-interactive mode instead
        assert _SPACK_PYTHON_INTERACTIVE_MSG in r.stdout

    def test_interactive_dash_i_shows_helpful_message(self, spack_root):
        """'spack python -i' alone (no script) should also avoid ESPIPE."""
        r = run_in_shell("spack python -i", extra_env={"SPACK_ROOT": spack_root})
        assert r.returncode == 0
        assert "Errno 29" not in r.stdout
        assert "I/O error" not in r.stdout
        assert _SPACK_PYTHON_INTERACTIVE_MSG in r.stdout

    def test_interactive_dash_i_dash_c_shows_helpful_message(self, spack_root):
        """'spack python -i -c ...' forces a REPL after running code; must not hit ESPIPE."""
        r = run_in_shell(
            'spack python -i -c "import spack"',
            extra_env={"SPACK_ROOT": spack_root},
        )
        assert r.returncode == 0
        assert "Errno 29" not in r.stdout
        assert "I/O error" not in r.stdout
        assert _SPACK_PYTHON_INTERACTIVE_MSG in r.stdout
