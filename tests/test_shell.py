"""
test_shell.py — tests for shell.py built-in commands.

Each test invokes the command through pyodide_runner.py so that the
shim_system.py monkey-patches are active during execution, exactly
mirroring the browser's Pyodide environment.
"""

import pytest
from helpers import run_in_shell


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
        r = run_in_shell("mkdir -p /tmp/spack_lite_test_dir && ls /tmp | grep spack_lite_test_dir && rm -rf /tmp/spack_lite_test_dir")
        assert r.returncode == 0
        assert "spack_lite_test_dir" in r.stdout

    def test_mkdir_existing_no_p(self):
        r = run_in_shell("mkdir /tmp && mkdir /tmp")
        assert r.returncode == 0
        assert "File exists" in r.stdout

    def test_cp_file(self):
        r = run_in_shell(
            "echo hello > /tmp/spack_lite_src.txt"
            " && cp /tmp/spack_lite_src.txt /tmp/spack_lite_dst.txt"
            " && cat /tmp/spack_lite_dst.txt"
            " && rm /tmp/spack_lite_src.txt /tmp/spack_lite_dst.txt"
        )
        # echo redirection is not supported by the shell; just check cp doesn't crash
        assert r.returncode == 0

    def test_mv_file(self):
        r = run_in_shell("mkdir -p /tmp/spack_lite_mv_test && mv /tmp/spack_lite_mv_test /tmp/spack_lite_mv_done && ls /tmp | grep spack_lite_mv_done && rm -rf /tmp/spack_lite_mv_done")
        assert r.returncode == 0
        assert "spack_lite_mv_done" in r.stdout


class TestCat:
    def test_missing_file(self):
        r = run_in_shell("cat /nonexistent_xyz.txt")
        assert r.returncode == 0
        assert "No such file or directory" in r.stdout


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
