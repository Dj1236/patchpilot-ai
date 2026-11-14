# Third-party licenses

PatchPilot AI is released under the MIT License. The Python package does
not copy third-party source code into this repository; its dependencies are
installed from PyPI at the versions pinned in `uv.lock`. The static landing
page in `site/` vendors a few unmodified files, listed at the end.

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
| httpx2, httpcore2 | BSD-3-Clause |
| jiter, docstring-parser, truststore | MIT |
| sniffio | MIT OR Apache-2.0 |
| google-genai, google-auth, requests, tenacity, distro | Apache-2.0 |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| urllib3, charset-normalizer | MIT |
| cffi | MIT-0 |
| websockets, pycparser | BSD-3-Clause |
| pyasn1 | BSD-2-Clause |
| pyasn1-modules | BSD |

`httpx2-jsfetch` appears in `uv.lock` for other platforms. It is not installed
here, so its license was not read from metadata.

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

## Landing page (`site/`), vendored and unmodified

Downloaded from the npm registry and copied without changes; the page loads
them locally and makes no network requests. They are not part of the Python
package.

| File | Package and version | License |
|---|---|---|
| `site/vendor/three.module.min.js` | three 0.170.0 | MIT (`site/vendor/three.LICENSE`) |
| `site/vendor/gsap.min.js`, `ScrollTrigger.min.js`, `SplitText.min.js` | gsap 3.13.0 | GSAP Standard "no charge" license, https://gsap.com/standard-license (stated in each file's header) |
| `site/assets/fonts/space-grotesk-latin-wght-normal.woff2` | @fontsource-variable/space-grotesk 5.3.0 | SIL OFL 1.1 (`site/assets/fonts/space-grotesk.OFL.txt`) |
| `site/assets/fonts/jetbrains-mono-latin-wght-normal.woff2` | @fontsource-variable/jetbrains-mono 5.3.0 | SIL OFL 1.1 (`site/assets/fonts/jetbrains-mono.OFL.txt`) |
