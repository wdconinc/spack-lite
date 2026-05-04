# spack-lite

**Spack-Lite** runs the [Spack Package Manager](https://github.com/spack/spack)
entirely inside a browser tab, using [Pyodide](https://pyodide.org/) (WebAssembly
Python) and an [xterm.js](https://xtermjs.org/) terminal emulator.

---

## Features

### Spack commands

| Command | Description |
|---|---|
| `spack list [query]` | List available packages (optionally filtered) |
| `spack info <pkg>` | Show package metadata, variants, and dependencies |
| `spack spec <spec>` | Concretize a spec and display the dependency tree |

### Built-in shell commands

| Command | Description |
|---|---|
| `ls [-la] [path]` | List directory contents |
| `cd [path]` | Change directory (`~` supported) |
| `pwd` | Print working directory |
| `cat <file>` | Print file contents |
| `echo <text>` | Print text |
| `head [-n N] [file]` | Print first N lines (default 10) |
| `tail [-n N] [file]` | Print last N lines (default 10) |
| `grep [-i] [-v] [-n] <pat> [file]` | Search for pattern |
| `mkdir [-p] <path>` | Create directory |
| `rm [-rf] <path>` | Remove file or directory |
| `cp [-r] <src> <dst>` | Copy file or directory |
| `mv <src> <dst>` | Move file or directory |
| `env` | Show environment variables |
| `which <cmd>` | Locate a command or built-in |
| `find [path] [-name pat] [-type f\|d]` | Find files |
| `help` | Show available commands |
| `clear` | Clear the terminal |

Commands can be chained with pipes (`\|`) and support `$VAR` / `${VAR}` expansion.

---

## Architecture

```
Browser Tab
├── index.html          ← xterm.js UI + message bus
└── worker.js           ← Web Worker
    ├── Pyodide         ← WebAssembly Python runtime
    ├── shim_system.py  ← "System lie": patches subprocess / os / platform
    ├── shell.py        ← Python-backed POSIX shell (ls, cd, cat, grep, …)
    ├── ~/.spack/       ← Injected compiler + package config
    └── /home/pyodide/spack/   ← spack-lite.tar.gz unpacked into MEMFS
```

Because Spack is a POSIX application that relies heavily on `subprocess`, the
`shim_system.py` module intercepts every shell-out call before Spack can attempt
a real `fork`/`exec` (which is unavailable in WASM) and returns hard-coded
responses that make Spack believe it is running on a standard **Linux x86_64**
host with GCC 11 available.

---

## Quick Start

### 1. Build the Spack-Lite archive

The browser loads a stripped-down copy of Spack called `spack-lite.tar.gz`.
Build it with the helper script (requires `git`, `rsync`, `tar`):

```bash
# Clone the repo and run the build script
git clone https://github.com/wdconinc/spack-lite.git
cd spack-lite
bash scripts/make_spack_lite.sh
# → produces spack-lite.tar.gz (~10–20 MB) in the repo root
```

The script:
1. Clones Spack `develop` branch (configurable via `SPACK_VERSION=vX.Y.Z` or `SPACK_VERSION=develop`)
2. Strips `.git`, tests, docs, and all but ~35 demo packages
3. Injects `spack_config/` (fake compiler + package prefs) into `etc/spack/`
4. Packs everything as `spack-lite.tar.gz`

### 2. Serve the app

The app **must** be served over HTTP (not `file://`) because of Web Worker and
`fetch()` restrictions:

```bash
python3 -m http.server 8080
# Open http://localhost:8080
```

> **Tip:** For development you can also use `npx serve .` or any other static
> file server.

### 3. Use the terminal

Once the status badge turns green (**Ready**), type commands:

```
spack-lite:~$ spack list
spack-lite:~$ spack info zlib
spack-lite:~$ spack spec zlib+shared
spack-lite:~$ ls /home/pyodide/spack/lib/spack | head -20
spack-lite:~$ cat /home/pyodide/spack/var/spack/repos/builtin/packages/zlib/package.py
spack-lite:~$ help
spack-lite:~$ clear
```

---

## Repository Layout

```
spack-lite/
├── index.html              ← Main page (xterm.js terminal UI)
├── worker.js               ← Pyodide Web Worker
├── shell.py                ← Python-backed POSIX shell interpreter
├── shim_system.py          ← Subprocess / platform / os monkey-patches
├── spack_config/
│   ├── compilers.yaml      ← Fake GCC 11 compiler definition
│   ├── packages.yaml       ← Provider preferences (openmpi, openblas …)
│   └── config.yaml         ← Core Spack config (concretizer, no checksum …)
├── scripts/
│   └── make_spack_lite.sh  ← Builds spack-lite.tar.gz
└── README.md
```

---

## Configuration Files

### `spack_config/compilers.yaml`

Defines a virtual **GCC 11.4.0** pointing at `/usr/bin/gcc`.  
The compiler never actually runs; all invocations are intercepted by the shim.

### `spack_config/packages.yaml`

Sets sensible provider defaults (`openmpi` for MPI, `openblas` for BLAS/LAPACK)
and locks the target microarchitecture to `x86_64`.

### `spack_config/config.yaml`

Disables checksum verification and SSL (unnecessary for a static demo), and sets
`concretizer: clingo`.

---

## Shell Design (`shell.py`)

`shell.py` implements a Python-backed POSIX-like shell that handles all
terminal commands, including `spack`.  It is loaded by the Web Worker after
`shim_system.py` and exposes a single public function:

```python
result_json = run_shell_command(line)  # returns {"output": "...", "cwd": "..."}
```

Key design points:

- **Pipeline support** — commands are split on unquoted `|` characters; each
  stage's stdout becomes the next stage's stdin.
- **Variable expansion** — `$VAR` and `${VAR}` are expanded from `os.environ`
  before each token is evaluated.
- **`spack` routing** — the `spack` built-in delegates to `spack.main.SpackCommand`,
  capturing stdout/stderr in a buffer so the output flows through the shell's
  normal result path.
- **CWD tracking** — `run_shell_command` returns the updated current working
  directory after each command so the terminal prompt stays accurate.

---

## Monkey-Patching Design (`shim_system.py`)

The shim is intentionally loaded **before** any Spack import.  It patches:

| Target | Replacement | Purpose |
|---|---|---|
| `sys.platform` | `"linux"` | Spack gates Linux-only code on this value |
| `os.name` | `"posix"` | POSIX path logic |
| `os.uname()` | `_FakeUname` | Reports `Linux x86_64 5.15.0` |
| `platform.*` | Lambdas | Machine / system / processor queries |
| `subprocess.run` | `_mock_run` | Returns hard-coded byte strings |
| `subprocess.Popen` | `_MockPopen` | Same; supports `communicate()` |
| `subprocess.check_output` | Wrapper | Returns `stdout` bytes |
| `grp` / `pwd` modules | Stub modules | May be absent in Pyodide |

---

## Known Limitations

| Issue | Notes |
|---|---|
| **No real installation** | `spack install` is not supported — no build tools exist in the browser |
| **Clingo WASM** | `spack spec` uses clingo for concretization; a WASM build is not yet bundled — use `spack config set concretizer:solver original` as a workaround with older Spack versions |
| **Memory** | Pyodide + Spack source use ~200–400 MB of RAM in the browser tab |
| **First load** | Downloading Pyodide (~10 MB) and spack-lite.tar.gz adds startup latency |
| **SharedArrayBuffer** | The worker uses `importScripts` which requires the page to be served with the correct COOP/COEP headers if SharedArrayBuffer is needed |

---

## Development

### Adjusting the package set

Edit the `KEEP_PKGS` array in `scripts/make_spack_lite.sh` and re-run the script.

### Changing the Spack version

```bash
# Use a specific release tag
SPACK_VERSION=v0.22.0 bash scripts/make_spack_lite.sh
# Use the develop branch (default)
SPACK_VERSION=develop bash scripts/make_spack_lite.sh
```

### Updating the shims

Edit `shim_system.py`.  The worker fetches it at runtime via `fetch('shim_system.py')`,
so a browser reload picks up changes immediately (no rebuild needed).

### Adding or modifying shell commands

Edit `shell.py`.  Like `shim_system.py`, it is fetched at runtime, so a
browser reload is sufficient.  Add a new `_cmd_<name>` function and register
it in the `_BUILTINS` dictionary at the bottom of the file.

---

## References

- [Spack documentation](https://spack.readthedocs.io/)
- [Pyodide documentation](https://pyodide.org/en/stable/)
- [xterm.js](https://xtermjs.org/)
- [Emscripten MEMFS](https://emscripten.org/docs/api_reference/Filesystem-API.html)