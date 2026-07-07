# Deploying to stockpicker.landryfam.com

Docker container + Cloudflare Tunnel. Nothing is port-forwarded: `cloudflared`
dials **out** to Cloudflare and proxies `stockpicker.landryfam.com` to the
webapp over the compose network. The webapp itself is only reachable from
`127.0.0.1` on the host and from the tunnel.

```
browser ──HTTPS──> Cloudflare edge ──tunnel──> cloudflared container ──HTTP──> webapp:8713
```

## Prerequisites

- `landryfam.com` active on Cloudflare (nameservers pointed there).
- Docker Engine + the compose plugin:

  ```bash
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker $USER   # then log out/in (or `newgrp docker`)
  ```

## 1. Create the tunnel (Cloudflare dashboard)

1. [Zero Trust dashboard](https://one.dash.cloudflare.com) → **Networks →
   Tunnels → Create a tunnel → Cloudflared**.
2. Name it `stockpicker`, save.
3. On the connector page, copy the token — the long string after `--token`
   in any of the install commands. That's all you need from that page
   (the connector runs in compose; don't install it on the host).
4. **Public Hostname** tab → **Add a public hostname**:
   - Subdomain: `stockpicker` · Domain: `landryfam.com`
   - Service Type: `HTTP` · URL: `webapp:8713`

   (`webapp` is the compose service name; cloudflared resolves it on the
   compose network.) This also creates the DNS record automatically.

## 2. Create a login — do this before going live

The webapp has its own login: every page and API call requires a session
cookie, obtained at `/login` with a username + password. Accounts are
managed from the CLI (no self-registration); passwords are scrypt-hashed
in `webapp_users.json` and sessions are signed with `webapp_secret.key`
(both git-ignored, both bind-mounted so they survive rebuilds):

```bash
docker compose exec webapp python webapp.py adduser <name>   # prompts twice
docker compose exec webapp python webapp.py passwd  <name>   # also logs that user out everywhere
docker compose exec webapp python webapp.py deluser <name>
docker compose exec webapp python webapp.py users
```

Failed logins are throttled (per-IP lockout after 10 misses in 15 min).
With **zero** users configured nobody can log in at all — create at least
one account before sharing the URL.

Cloudflare Access in front of this is optional belt-and-suspenders; the
app no longer depends on it.

## 3. Configure and launch

```bash
cp .env.example .env      # paste CLOUDFLARE_TUNNEL_TOKEN, set PUID/PGID (id -u; id -g)
docker compose up -d --build
```

## 4. Verify

```bash
docker compose ps                        # webapp healthy, cloudflared running
docker compose logs cloudflared | tail   # "Registered tunnel connection" x4
curl -s http://127.0.0.1:8713/api/status | head -c 200   # local sanity check
```

Then open **https://stockpicker.landryfam.com** — you should land on the
sign-in page, and the dashboard after logging in.

## Operations

```bash
docker compose logs -f webapp     # job logs also stream in the UI
docker compose up -d --build      # redeploy after a git pull
docker compose down               # stop (tunnel goes offline, DNS stays)
```

State lives on the host, not in the container: the repo is bind-mounted, so
`data_cache/` and the layer 5/6 artifacts (`layer6_portfolio.json`, etc.) are
the same files the CLI uses and survive rebuilds. Back up
`layer6_portfolio.json` if you care about the current spec.

## Alternative: CLI-managed tunnel (no dashboard token)

`cloudflared` is installed on this host, so you can manage the tunnel from
the terminal instead; config then lives in `~/.cloudflared/`:

```bash
cloudflared tunnel login                                  # browser auth, pick landryfam.com
cloudflared tunnel create stockpicker
cloudflared tunnel route dns stockpicker stockpicker.landryfam.com
```

Then replace the `cloudflared` service command/token in `docker-compose.yml`
with a mounted config:

```yaml
  cloudflared:
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    command: tunnel run stockpicker
    volumes:
      - ~/.cloudflared:/etc/cloudflared:ro
```

and put in `~/.cloudflared/config.yml`:

```yaml
tunnel: stockpicker
credentials-file: /etc/cloudflared/<TUNNEL_UUID>.json
ingress:
  - hostname: stockpicker.landryfam.com
    service: http://webapp:8713
  - service: http_status:404
```

The dashboard-token route (steps above) is simpler; use this only if you
prefer config-as-files.
