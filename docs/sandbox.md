# Sandbox

The sandbox runs approved pytest commands against a source tree inside a
restricted container and returns a structured result.

**It reduces risk. It does not guarantee isolation against hostile code.** A
container shares the kernel of the Docker host, and a kernel or container
runtime vulnerability can defeat every control listed here. PatchPilot
therefore runs only approved repositories.

## Lifecycle of one run

1. The source tree is read on the host into memory. Nothing is executed or
   imported. Symbolic links, special files and oversized trees are refused.
2. A container starts from the PatchPilot image and does nothing but sleep.
3. The tree is streamed into the container's workspace as a tar archive.
4. A launcher baked into the image starts pytest with a configuration file
   from the image, under a host-side timeout. Configuration files in the
   workspace are not read.
5. Limit events and the JUnit report are read back out.
6. The container is force-removed, whatever happened, and the runner
   confirms it is gone. The engine also removes it by itself if the host
   process dies.

Nothing from the host is mounted into the container. The workspace exists
only in the container's memory and disappears with it.

## Controls and how each is verified

"Probe" means a test that attempts the forbidden action from inside a real
container and passes only if it is refused
(`tests/integration/test_sandbox_security.py`,
`tests/integration/test_sandbox_limits.py`).

| Control | Setting | Verified by |
|---|---|---|
| No network | `--network none` | Probe: TCP, UDP and DNS all fail; only loopback exists |
| Unprivileged user | `--user 10001:10001` | Probe: UID and GID, cannot become root |
| No capabilities | `--cap-drop ALL` | Probe: all five capability sets are zero |
| No privilege gain | `no-new-privileges`, default seccomp profile, no setuid files in the image | Probe: process flags, filesystem scan |
| Read-only root | `--read-only` | Probe: tries to create a file in every directory |
| Confined writes | Workspace and `/tmp` are size-capped, non-executable memory filesystems; `/dev/shm` capped at 8 MB | Probe: fills them until the cap, tries to execute from them |
| No host files | No mounts at all | Probe: mount table has no shared or writable disk filesystem |
| No Docker socket | Never mounted | Probe |
| No host secrets | No environment variable is forwarded; the docker client itself gets an allowlisted environment | Probe with planted canaries in the host environment and on the host disk |
| Memory limit | `--memory`, swap disabled | Probe allocates past the limit; run is reported as `out_of_memory` |
| Process limit | `--pids-limit` | Probe spawns processes until refused; event is reported |
| CPU limit | `--cpus` | Probe runs three busy processes for two seconds at a quarter-core limit; the kernel's accounting shows under 0.4 of a core used and throttling. Measured locally: 0.25 |
| Time limit | Host timer, then `docker kill` | Probe ignores signals and sleeps; run is reported as `timeout` |
| Output limit | Bounded capture on the host | Probe prints 20 MB; output is truncated and flagged |
| Cleanup | `docker rm --force` in a `finally` block, confirmed by listing; `--rm` plus a sleep backstop if the host process dies | Every integration test asserts no container is left; a failed removal is reported as `cleanup_failed` |
| Controlled image | Base pinned by digest, hash-checked wheels only, no repository code in the build | Unit test on the image definition |
| Fixed command | Only pytest targets and source roots are configurable, validated as plain relative paths | Unit tests |
| Trusted runner | Launcher and pytest configuration come from the image and are read-only | Probes: a `pytest.py` or `pytest.ini` in the workspace has no effect |
| Report consistency | Exit status is cross-checked against the JUnit report | Unit and integration tests |

### Control tests

A probe that cannot fail proves nothing. `test_sandbox_controls.py` runs the
same probes in deliberately weakened containers (network on, root user,
writable root) and checks that they then fail.

## Result statuses

| Status | Meaning | Blames |
|---|---|---|
| `passed` | pytest exit code 0 | n/a |
| `failed` | At least one test failed | Code under test |
| `collection_error` | pytest could not import or collect the tests | Code under test |
| `no_tests_collected` | No test matched the targets | Task definition |
| `runner_error` | pytest usage or internal error | Task definition |
| `timeout` | The time budget ran out; the container was killed | Code under test |
| `out_of_memory` | The kernel killed a process for exceeding the memory limit | Code under test |
| `infrastructure_error` | The sandbox itself failed | Nothing: says nothing about the code |

An out-of-memory kill takes precedence over whatever pytest reported, because
results from a run that lost a process are not reliable.

## Reproducibility metadata

Every result records the runner version, image tag and image ID, Docker
engine version and platform, control-group version, host platform, the
SHA-256 digest of the source tree, the source roots and the limits used.

Two runs of the same tree in the same image give the same test outcomes for
deterministic tests. Durations differ between runs.

## Unresolved limitations

- **Shared kernel.** See the top of this page. Stronger isolation (gVisor or
  a microVM) is a requirement before accepting untrusted repositories.
- **Results come from inside the sandbox.** The JUnit report and the exit
  status come from a process that runs the code under test. They are
  cross-checked against each other, but code that patches pytest from inside
  that process can forge both. See [grading-integrity.md](grading-integrity.md).
- **The image is found by its local tag.** The tag is derived from the image
  definition and the image ID is recorded with every result, but an image
  with that tag built by someone else on the same machine would be used.
- **Source trees are read without locking.** Symbolic links are refused, but
  a file swapped while it is being read, or a hard link to a file outside
  the tree, is not detected. Trees come from PatchPilot's own checkouts.
- **File names are restricted** to letters, digits, `_`, `.` and `-`. A
  repository with other characters in a file name is refused.
- **If the container itself dies** (not just the test process), the engine
  removes it and the cause is reported as an infrastructure error.
- **The image build needs network access.** It fetches a digest-pinned base
  image and hash-checked wheels, and runs no repository code, but it does
  trust Docker Hub and PyPI to serve those exact artifacts.
- **The base image is pinned, its contents are not audited.** Known
  vulnerabilities in the base image are not scanned for yet.
- **Scratch space counts against memory.** Filling the workspace or `/tmp`
  uses memory from the same limit, so limits are validated to leave headroom.
- **One image.** Tasks cannot yet declare their own pinned dependencies.
- **Side channels and denial of service inside the limits** are out of scope.

## Platform notes

| | Local development (macOS) | Reference environment (Linux CI) |
|---|---|---|
| Where containers run | Inside a Linux virtual machine (Colima) | Directly on the runner's kernel |
| Extra isolation layer | Yes, the virtual machine | No |
| Kernel under test | The virtual machine's kernel | The runner's kernel |
| Status | Sandbox tests run and pass | **Not run**: see [ci-status.md](ci-status.md) |

The virtual machine on macOS means a container escape lands in the virtual
machine, not on the Mac. That extra layer does not exist on Linux, so passing
locally is not proof for Linux. Sandbox tests need control groups version 2
to report memory and process events; on version 1, an out-of-memory kill is
detected only when Docker itself records it.

## Running the sandbox tests

```
colima start                 # macOS only; any Docker engine works
uv run pytest -m sandbox
```

Without a reachable Docker engine these tests are skipped, not passed.
