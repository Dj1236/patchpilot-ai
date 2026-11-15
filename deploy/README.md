# Deploying the live demo (gate F)

The demo is `patchpilot.demo.app`: a FastAPI service that runs PatchPilot's
normal repair pipeline with a local open-weight model (Ollama, `qwen3:8b`)
and the Docker sandbox, on the four built-in synthetic tasks only. It needs
one Linux host with Docker, about 8 GB of free memory and no GPU.
**Status: deployed** at <https://54-252-135-177.sslip.io/> on an AWS
Free-plan `m7i-flex.large` with Groq instead of Ollama; see
[docs/hosted-demo.md](../docs/hosted-demo.md).

With Groq (`PATCHPILOT_DEMO_PROVIDER=groq`), skip Ollama: add
`GROQ_API_KEY` to `/etc/patchpilot/demo.env` (written through stdin, never
on a command line) and the host needs only Docker, about 1 GB of memory in
use. `PATCHPILOT_DEMO_PER_DAY`, `PATCHPILOT_DEMO_PER_ADDRESS_PER_HOUR` and
`PATCHPILOT_DEMO_MAX_QUEUE` set the public limits (5, 2 and 1 on the live
host).

## Controls

| Control | Where |
|---|---|
| Only the four `inj-*` synthetic tasks; no other input is accepted | `create_app` (422) |
| Optional shared access code (`PATCHPILOT_DEMO_ACCESS_CODE`) | `X-Demo-Code` header, constant-time compare (401) |
| 3 jobs per address per hour, 40 per day, queue of 3, one job at a time | `Limits`, env-overridable (429/503) |
| Every agent budget unchanged: 3 proposals, 400,000 tokens, 30 generation attempts, 1,200 s, tool budgets | `RepairLimits`, `RunLimits` |
| Sandbox: no network, read-only root, non-root, no capabilities, memory, CPU, PID and time limits | `patchpilot.sandbox` |
| No GitHub token, no submit; Ollama and the app listen on 127.0.0.1 only | `ollama_client.py`, systemd unit |
| No API docs or OpenAPI schema exposed; 4 KB request bodies; TLS and security headers | `create_app`, `Caddyfile` |
| Only a sha256 prefix of each address is kept, in memory | `create_app` |

Residual risk: the host runs Docker for the sandbox, so the service account
can start containers; use a dedicated VM with nothing else on it.

## Host choice

Oracle Cloud Always Free (Ampere A1, arm64) is the only no-cost option found
that runs Docker. Its free allowance is now reported as 2 OCPU and 12 GB
(previously 4 OCPU and 24 GB), and free capacity is often unavailable.
`qwen3:8b` fits in 12 GB, but **CPU-only speed on 2 cores is unmeasured**; a
run took 35 to 65 s on an Apple M5 and may take several minutes there, close
to the agent's 1,200 s budget. Measure it before announcing the URL.

## Steps (Ubuntu 24.04, arm64 or x86-64)

```bash
# 1. Account and VM (owner): Oracle Cloud sign-up, then Compute > Instance,
#    shape VM.Standard.A1.Flex, 2 OCPU / 12 GB, Ubuntu 24.04. Open ports 80
#    and 443 in the VCN security list. Keep the SSH key private.

# 2. Packages
sudo apt-get update && sudo apt-get install -y docker.io git curl caddy
curl -fsSL https://ollama.com/install.sh | sh        # read the script before running it
sudo systemctl enable --now docker ollama
ollama pull qwen3:8b

# 3. Service account and code
sudo useradd --system --create-home --home-dir /var/lib/patchpilot patchpilot
sudo usermod -aG docker patchpilot
sudo git clone https://github.com/Dj1236/patchpilot-ai /opt/patchpilot-ai
sudo chown -R patchpilot /opt/patchpilot-ai
curl -LsSf https://astral.sh/uv/install.sh | sudo -u patchpilot sh
sudo -u patchpilot bash -lc 'cd /opt/patchpilot-ai && ~/.local/bin/uv sync --locked --extra demo --no-dev'

# 4. Optional access code, then the service and TLS
sudo install -d -m 700 /etc/patchpilot
echo "PATCHPILOT_DEMO_ACCESS_CODE=$(openssl rand -hex 8)" | sudo tee /etc/patchpilot/demo.env
sudo cp deploy/patchpilot-demo.service /etc/systemd/system/
sudo systemctl enable --now patchpilot-demo
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile   # set your domain first
sudo systemctl reload caddy

# 5. Verify: the first run also builds the sandbox image (needs network once)
curl -s https://<domain>/api/health
```

Then open `https://<domain>/`, run `inj-004-pager`, and check the outcome,
hidden-test verdict and duration before linking the demo from the website.
