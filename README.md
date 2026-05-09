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
├── index.html               ← xterm.js UI + message bus
└── worker.js                ← Web Worker
    ├── Pyodide 0.27.3       ← WebAssembly Python runtime (Python 3.12)
    │   └── clingo 5.7.1     ← ASP solver, bundled in Pyodide — loaded via loadPackage()
    ├── wasm-git 0.0.14      ← libgit2 compiled to WASM; exposes self.gitCall for git ops
    ├── shim_system.py       ← "System lie": patches subprocess / os / platform
    ├── shell.py             ← Python-backed POSIX shell (ls, cd, cat, grep, …)
    ├── ~/.spack/            ← Injected compiler + package config (written at startup)
    └── /home/pyodide/spack/ ← spack-lite.tar.gz (seed packages) + spack-packages.tar.gz
                               (all packages, overlaid lazily in the background)
```

Because Spack is a POSIX application that relies heavily on `subprocess`, the
`shim_system.py` module intercepts every shell-out call before Spack can attempt
a real `fork`/`exec` (which is unavailable in WASM) and returns hard-coded
responses that make Spack believe it is running on a standard **Linux x86_64**
host with GCC 11 available.

Clingo 5.7.1 (the ASP solver used by `spack spec` and `spack concretize`) is
bundled directly in Pyodide 0.27.3 and is loaded via `pyodide.loadPackage('clingo')`
— no custom WASM build or separate wheel download is required.

The Spack package repository was split into
[spack/spack-packages](https://github.com/spack/spack-packages) in Package API
v2.x. `make_spack_lite.sh` clones both `spack/spack` and `spack/spack-packages`
and produces two archives:

- **`spack-lite.tar.gz`** — core Spack + a seed set of ~40 demo packages,
  available immediately when the browser interface becomes active.
- **`spack-packages.tar.gz`** — all Spack built-in package recipes, fetched
  lazily in the background so the full catalogue is available without delaying
  the initial page load.

---

## Quick Start

### 1. Build the Spack-Lite archive

The browser loads a stripped-down copy of Spack. Build both archives with the
helper script (requires `git`, `rsync`, `tar`):

```bash
# Clone the repo and run the build script
git clone https://github.com/wdconinc/spack-lite.git
cd spack-lite
bash scripts/make_spack_lite.sh
# → produces spack-lite.tar.gz and spack-packages.tar.gz in the repo root
```

The script:
1. Clones Spack `develop` branch (configurable via `SPACK_VERSION=vX.Y.Z`)
2. Clones `spack/spack-packages` for the package recipes (configurable via
   `SPACK_PACKAGES_VERSION=develop`)
3. Strips `.git`, tests, docs, and large assets from the core
4. Injects `spack_config/` (fake compiler + package prefs) into `etc/spack/`
5. Packs a seed set of ~40 demo packages as `spack-lite.tar.gz`
6. Packs all built-in package recipes as `spack-packages.tar.gz` (lazy-loaded)

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
spack-lite:~$ cat /home/pyodide/spack/var/spack/repos/spack_repo/builtin/packages/zlib/package.py
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
│   ├── concretizer.yaml    ← Concretizer config (reuse: false for browser env)
│   ├── packages.yaml       ← Provider preferences + fake GCC 11 external spec
│   ├── config.yaml         ← Core Spack config (concretizer: clingo, no checksum …)
│   └── repos.yaml          ← Override builtin repo to local path (no git needed)
├── scripts/
│   ├── make_spack_lite.sh          ← Builds spack-lite.tar.gz + spack-packages.tar.gz
│   ├── build_clingo_pyodide.sh     ← (CI) Builds a Pyodide clingo wheel from source
│   ├── build_local.py              ← Bundles worker inline for local file:// testing
│   ├── check_pyodide_version_sync.py ← CI: verifies PYODIDE_VERSION matches worker.js
│   ├── smoke_test_clingo_pyodide.mjs ← Node smoke-test for the clingo wheel
│   └── smoke_test_site_browser.mjs   ← Playwright browser smoke-test for the full site
└── README.md
```

---

## Configuration Files

### `spack_config/concretizer.yaml`

Sets `reuse: false` to prevent Spack from attempting to reuse previously
installed packages.  In the browser/Pyodide environment no packages are ever
actually installed, so the reuse check (which requires libc compatibility
information from compiler packages) would always fail with Spack ≥ 2025.

### `spack_config/packages.yaml`

Sets sensible provider defaults (`openmpi` for MPI, `openblas` for BLAS/LAPACK),
locks the target microarchitecture to `x86_64`, and declares a fake external
`gcc@11.4.0` at `/usr` so Spack does not try to bootstrap a compiler.

The GCC 11 compiler is also written into `~/.spack/linux/compilers.yaml` at
startup by `worker.js`.  All compiler invocations are intercepted by the shim.

### `spack_config/config.yaml`

Disables checksum verification and SSL (unnecessary for a static demo), and sets
`concretizer: clingo`.

### `spack_config/repos.yaml`

Overrides the builtin repo URL to point at the local path inside the unpacked
archive (`$spack/var/spack/repos/spack_repo/builtin`) so Spack does not attempt
to clone packages from GitHub.

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
# Override the spack-packages branch as well
SPACK_PACKAGES_VERSION=develop bash scripts/make_spack_lite.sh
```

### Updating the shims

Edit `shim_system.py`.  The worker fetches it at runtime via `fetch('shim_system.py')`,
so a browser reload picks up changes immediately (no rebuild needed).

### Adding or modifying shell commands

Edit `shell.py`.  Like `shim_system.py`, it is fetched at runtime, so a
browser reload is sufficient.  Add a new `_cmd_<name>` function and register
it in the `_BUILTINS` dictionary at the bottom of the file.

### Local file:// testing

`scripts/build_local.py` bundles `worker.js`, `shim_system.py`, and `shell.py`
into a self-contained `local/index.html` that can be opened directly from the
filesystem without a web server.

### Running the test suite

```bash
pip install pytest pytest-timeout clingo
pytest tests/ -v --timeout=300
```

CI also runs a Playwright Chromium browser smoke-test (`scripts/smoke_test_site_browser.mjs`)
against a fully-assembled `_site/` directory to catch worker startup regressions.

---

## References

- [Spack documentation](https://spack.readthedocs.io/)
- [Pyodide documentation](https://pyodide.org/en/stable/)
- [xterm.js](https://xtermjs.org/)
- [Emscripten MEMFS](https://emscripten.org/docs/api_reference/Filesystem-API.html)
