# Third-party licenses

PatchPilot AI is released under the MIT License. It does not copy
third-party source code into this repository; dependencies are installed
from PyPI at the versions pinned in `uv.lock`.

Licenses below were read from each installed package's metadata. Re-check
this list whenever a dependency is added.

## Runtime dependencies

| Package | License |
|---|---|
| typer | MIT |
| pydantic, pydantic-core | MIT |
| pydantic-settings | MIT |
| annotated-types, annotated-doc, typing-inspection | MIT |
| rich, markdown-it-py, mdurl | MIT |
| typing-extensions | PSF-2.0 |
| python-dotenv | BSD-3-Clause |
| pygments | BSD-2-Clause |
| shellingham | ISC |
| httpx, httpcore, idna | BSD-3-Clause |
| h11, anyio | MIT |
| certifi | MPL-2.0 |
| anthropic, opentelemetry-api | Apache-2.0 |
| httpx2, httpcore2, httpx2-jsfetch | BSD-3-Clause |
| jiter, docstring-parser, truststore | MIT |
| sniffio | MIT OR Apache-2.0 |

## Development-only dependencies

These are used to build and check the project and are not distributed with
it.

| Package | License |
|---|---|
| ruff | MIT |
| mypy, mypy-extensions, librt, ast-serialize | MIT |
| pytest, pytest-cov, pluggy, iniconfig | MIT |
| pre-commit, cfgv, identify, virtualenv, filelock, platformdirs, python-discovery, pyyaml | MIT |
| coverage | Apache-2.0 |
| packaging | Apache-2.0 OR BSD-2-Clause |
| nodeenv | BSD |
| distlib | PSF-2.0 |
| pathspec | MPL-2.0 |

## Sandbox image

The sandbox image is built locally and is not distributed with PatchPilot
AI. It is based on the official `python:3.12-slim` image (Debian packages
under their own licenses, CPython under PSF-2.0) and adds pytest, pluggy,
iniconfig (MIT), packaging (Apache-2.0 OR BSD-2-Clause) and pygments
(BSD-2-Clause), pinned in `src/patchpilot/sandbox/image/requirements.lock`.

## Local tools

Docker CLI (Apache-2.0) and Colima (MIT) are used on the developer machine
only.

All of these are compatible with distributing PatchPilot AI under the MIT
License. `pathspec` (development only) and `certifi` (a bundle of certificate
authority certificates, used unmodified) are under the weak-copyleft MPL-2.0.
