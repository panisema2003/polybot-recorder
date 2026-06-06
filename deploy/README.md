# Deploying the recorder to AWS EC2

The recorder is a tiny, long-running data-capture process. The goal of running
it on EC2 instead of your laptop is simple: **it must stay alive for ~2 weeks
without interruption** (no sleep, no reboot, no closed terminal). systemd gives
us auto-restart on crashes and on reboot.

This is read-only, public-data capture. No wallet, no API key, no money.

---

## 0. Launch the instance (one-time, in the EC2 console)

| Setting | Value |
|---|---|
| AMI | Ubuntu Server 24.04 LTS, **64-bit (x86)** |
| Instance type | `t3.micro` (free-tier eligible) — 2 vCPU / 1 GB is plenty |
| Key pair | create/download a `.pem` (your SSH login) |
| Storage | 20 GB **gp3** |
| Security group | inbound **SSH (22) from "My IP" only** — not 0.0.0.0/0. No other inbound. |
| Elastic IP | allocate + associate (keeps the IP stable across stop/start) |
| Termination protection | enable (Advanced details) |

Use **on-demand**, not Spot (Spot can be reclaimed and break continuity).

```bash
chmod 400 your-key.pem
ssh -i your-key.pem ubuntu@YOUR_ELASTIC_IP
```

## 1. Install prerequisites

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip git sqlite3
```

## 2. Create a service user + install the app

The repo is public, so it clones with no credentials.

```bash
sudo useradd --create-home --home-dir /opt/polybot --shell /bin/bash polybot
sudo -iu polybot   # become the polybot user

git clone https://github.com/panisema2003/polybot-recorder.git /opt/polybot/app
cd /opt/polybot/app
python3 -m venv .venv
.venv/bin/pip install -e .          # base only — NOT [analysis]; charting is local
mkdir -p data
exit                                # back to your sudo user
```

> Layout this produces (matches the service file exactly):
> `/opt/polybot/app` = repo root · `/opt/polybot/app/.venv` = venv ·
> `/opt/polybot/app/data/polybot.db` = capture file.

## 3. Install & start the service

```bash
sudo cp /opt/polybot/app/deploy/polybot-recorder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now polybot-recorder
```

## 4. Verify it's actually recording

```bash
# live logs — expect "REST refresh: N/N books" every ~30s
journalctl -u polybot-recorder -f

# row counts should climb over time
sudo -u polybot sqlite3 /opt/polybot/app/data/polybot.db \
  "SELECT source, COUNT(*) FROM book_top GROUP BY source;"
```

If you see repeated reconnects/HTTP errors, the instance IP may be rate-limited:
lower the basket (`--discover-top 5` in the unit's `ExecStart`) or raise
`rest_snapshot_interval_s` in `config.yaml`, then `daemon-reload` + `restart`.

## 5. Back up the database (recommended)

SQLite's `.backup` makes a safe copy even while it's being written. Add a daily
cron as the `polybot` user (`sudo -u polybot crontab -e`):

```cron
0 3 * * * sqlite3 /opt/polybot/app/data/polybot.db ".backup /opt/polybot/app/data/backup-$(date +\%F).db"
```

For extra safety, take periodic **EBS snapshots** of the volume from the console.

## 6. Pull the data to your laptop for analysis

```bash
# from your machine:
scp -i your-key.pem ubuntu@YOUR_ELASTIC_IP:/opt/polybot/app/data/polybot.db ./data/polybot.db
py scripts/analyze.py --list
py scripts/analyze.py --slug SOME_SLUG --out reports
```

---

## Using a curated basket instead of auto-discovery (recommended)

`--discover-top 10` picks whatever ranks highest *at startup* — which can drift
to short-dated markets that resolve mid-run. For a focused, reproducible run,
point the recorder at a **version-controlled basket file** and add the
resolution-horizon guard. Override the service non-interactively with a drop-in:

```bash
# get the latest code + basket files (editable install picks up changes on pull)
sudo -u polybot bash -lc 'cd /opt/polybot/app && git pull'

sudo mkdir -p /etc/systemd/system/polybot-recorder.service.d
sudo tee /etc/systemd/system/polybot-recorder.service.d/basket.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/opt/polybot/app/.venv/bin/python -m polybot record --basket /opt/polybot/app/baskets/colombia-runoff-2026.txt --min-days-to-resolution 1
EOF
sudo systemctl daemon-reload
sudo systemctl restart polybot-recorder
```

To change the basket later: edit the file in the repo, push, then on the box
`git pull` + `systemctl restart polybot-recorder`. (`--min-days-to-resolution N`
drops anything resolving within N days so you never record a market that dies
mid-run.)

## Operations cheatsheet

| Action | Command |
|---|---|
| Status | `systemctl status polybot-recorder` |
| Live logs | `journalctl -u polybot-recorder -f` |
| Restart | `sudo systemctl restart polybot-recorder` |
| Stop | `sudo systemctl stop polybot-recorder` |
| Change basket | edit `ExecStart` in the unit, `daemon-reload`, `restart` |
| Update code | `sudo -iu polybot`; `cd app && git pull`; `exit`; `restart` |

## Notes

- **Stop ≠ Terminate:** stopping keeps the EBS volume (and your data);
  terminating deletes it. Termination protection guards against accidents.
- **Time sync:** Ubuntu on EC2 uses the Amazon Time Sync service by default, so
  `recv_ms` timestamps are accurate.
- **Disk:** top-of-book rows are tiny; weeks of ~10 markets stay well under a
  few hundred MB.
