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

The live host is an AWS Free-plan `m7i-flex.large` (2 vCPU, 8 GiB,
Free-tier eligible) in `ap-southeast-2`, with `openai/gpt-oss-120b` on
Groq's free tier, so the host needs no model: about 1 GB is in use during a
run. On the Free plan AWS charges nothing and closes the account when the
credit is used up; an AWS Budget stops the instance at 75 USD. Do not
upgrade either account to a paid plan.

With Ollama instead, `qwen3:8b` needs more memory than the largest
Free-plan instance; the earlier Oracle Always Free option (2 OCPU, 12 GB) is
untested.

## Maintenance

SSH has no inbound rule. For maintenance, add a rule for your own `/32`,
do the work, and remove the rule, as one block (zsh):

```zsh
{ MYIP=$(curl -s -4 https://checkip.amazonaws.com)
  aws ec2 authorize-security-group-ingress --profile patchpilot --region ap-southeast-2 --group-id sg-059e62ff649993585 --ip-permissions "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=${MYIP}/32}]" >/dev/null
  sleep 3; ssh -i ~/.ssh/patchpilot-demo.pem ubuntu@54.252.135.177   # work here
} always { aws ec2 revoke-security-group-ingress --profile patchpilot --region ap-southeast-2 --group-id sg-059e62ff649993585 --ip-permissions "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=${MYIP}/32}]" >/dev/null; }
```

To read the access code into the clipboard without printing it, replace the
`ssh` line with:
`ssh -i ~/.ssh/patchpilot-demo.pem ubuntu@54.252.135.177 "sudo sed -n 's/^PATCHPILOT_DEMO_ACCESS_CODE=//p' /etc/patchpilot/demo.env" | tr -d '\n' | pbcopy`.
To rotate it, on the host: replace that line with a new `openssl rand -hex 8`
value and `sudo systemctl restart patchpilot-demo`.

## Steps (Ubuntu 24.04, arm64 or x86-64)

```bash
# 1. VM (owner): an instance with Docker support, Ubuntu 24.04, ports 80 and
#    443 open, SSH only from your own address. Keep the SSH key private.

# 2. Packages
sudo apt-get update && sudo apt-get install -y docker.io git curl caddy
sudo systemctl enable --now docker
# Ollama provider only (skip with Groq):
curl -fsSL https://ollama.com/install.sh | sh        # read the script before running it
sudo systemctl enable --now ollama && ollama pull qwen3:8b

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
