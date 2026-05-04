# spack-lite

**Spack-Lite** runs the [Spack Package Manager](https://github.com/spack/spack)
entirely inside a browser tab, using [Pyodide](https://pyodide.org/) (WebAssembly
Python) and an [xterm.js](https://xtermjs.org/) terminal emulator.

[![demo screenshot](docs/screenshot.png)](index.html)

---

## Features

| Command | Description |
|---|---|
| `spack list [query]` | List available packages (optionally filtered) |
| `spack info <pkg>` | Show package metadata, variants, and dependencies |
| `spack spec <spec>` | Concretize a spec and display the dependency tree |

---

## Architecture

```
Browser Tab
├── index.html          ← xterm.js UI + message bus
└── worker.js           ← Web Worker
    ├── Pyodide         ← WebAssembly Python runtime
    ├── shim_system.py  ← "System lie": patches subprocess / os / platform
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
1. Clones Spack `v0.21.0` (configurable via `SPACK_VERSION=vX.Y.Z`)
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
spack-lite $ spack list
spack-lite $ spack info zlib
spack-lite $ spack spec zlib+shared
spack-lite $ help
spack-lite $ clear
```

---

## Repository Layout

```
spack-lite/
├── index.html              ← Main page (xterm.js terminal UI)
├── worker.js               ← Pyodide Web Worker
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
SPACK_VERSION=v0.22.0 bash scripts/make_spack_lite.sh
```

### Updating the shims

Edit `shim_system.py`.  The worker fetches it at runtime via `fetch('shim_system.py')`,
so a browser reload picks up changes immediately (no rebuild needed).

---

## References

- [Spack documentation](https://spack.readthedocs.io/)
- [Pyodide documentation](https://pyodide.org/en/stable/)
- [xterm.js](https://xtermjs.org/)
- [Emscripten MEMFS](https://emscripten.org/docs/api_reference/Filesystem-API.html)