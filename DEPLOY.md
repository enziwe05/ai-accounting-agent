# Going live — deployment guide

This walks you from nothing to a live, always-on bookkeeping bot for the first
company (**M26**), on your own small server. Follow it top to bottom the first
time. Adding companies 2 and 3 later is a short repeat (see the end).

**What you're building:** one small server running one MySQL database server, the
M26 app, and Caddy (which gives a secure `https://` web address and renews the
certificate for you). Each company gets its own database, WhatsApp number and
invoice branding — their books never mix.

---

## 0. What you need before you start

- **A VPS** (small server). Hetzner (CX22, ~€4/mo) or DigitalOcean ($6/mo) are
  fine. Choose **Ubuntu 24.04**. You'll get an IP address and a root password (or
  SSH key).
- **A domain / subdomain you control.** M26 already owns `m26technologies.co.sz`,
  so we'll use **`books.m26technologies.co.sz`** — no new domain needed. You just
  need access to M26's DNS settings to add one record (Step 4).
- **A WhatsApp Business number for M26** with a **permanent** access token (Step
  6). This is separate from anyone's personal WhatsApp.
- **An Anthropic API key** (from console.anthropic.com).

> Tip: everywhere below, replace `books.m26technologies.co.sz` with the real
> address if you pick a different one, and run commands exactly as shown.

---

## 1. First login + a safe user

Log in to the server from your own computer's terminal:

```bash
ssh root@YOUR_SERVER_IP
```

Create a normal user (don't run everything as root) and give it admin rights:

```bash
adduser deploy
usermod -aG sudo deploy
```

From now on, log in as `deploy`:

```bash
ssh deploy@YOUR_SERVER_IP
```

## 2. Lock the server down (one-time)

```bash
# Turn on automatic security updates
sudo apt update && sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -f noninteractive unattended-upgrades

# Firewall: allow only SSH + web
sudo apt install -y ufw
sudo ufw allow OpenSSH
sudo ufw allow 80
sudo ufw allow 443
sudo ufw --force enable
```

## 3. Install Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker deploy
# Log out and back in so the group takes effect:
exit
ssh deploy@YOUR_SERVER_IP
docker --version   # should print a version
```

## 4. Point the web address at the server (DNS)

In M26's domain/DNS settings, add an **A record**:

| Type | Name | Value |
|------|------|-------|
| A | `books` | `YOUR_SERVER_IP` |

That makes `books.m26technologies.co.sz` point at your server. DNS can take a few
minutes to an hour. You can check with `ping books.m26technologies.co.sz`.

## 5. Get the code onto the server

```bash
sudo mkdir -p /opt/bookkeeper
sudo chown deploy:deploy /opt/bookkeeper
git clone https://github.com/enziwe05/ai-accounting-agent.git /opt/bookkeeper
cd /opt/bookkeeper
```

## 6. Get M26's WhatsApp number + permanent token (on Meta)

In the Meta developer dashboard (developers.facebook.com), for M26's app:
1. Add M26's WhatsApp Business number (a real number, not on personal WhatsApp).
2. Create a **System User** with a **permanent** access token (the test token
   expires in ~24h — don't use it for production).
3. Note down: the **access token**, the **Phone Number ID**, and the **App
   Secret** (App → Settings → Basic).

You'll paste these into the env file next. (The final webhook wiring is Step 9.)

## 7. Fill in M26's settings

Create the two config files from the templates:

```bash
cp env/db.env.example env/db.env
cp env/m26.env.example env/m26.env
nano env/db.env      # set one long random MYSQL_ROOT_PASSWORD, save (Ctrl+O, Enter, Ctrl+X)
nano env/m26.env     # fill in everything, save
```

In `env/m26.env` make sure you set:
- `DB_PASS` — **the same** value as `MYSQL_ROOT_PASSWORD` in `env/db.env`
- `ANTHROPIC_API_KEY`
- Business details (name, VAT no., banking line) for M26's invoices
- `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`
- `WHATSAPP_VERIFY_TOKEN` — invent any secret string (you'll reuse it in Step 9)
- `OWNER_NUMBERS` — the M26 numbers allowed to use the bot (digits, comma-separated)

## 8. Set the web address in Caddy, then launch

Edit the `Caddyfile` and put a real email + confirm the M26 address:

```bash
nano Caddyfile      # set the email line; confirm books.m26technologies.co.sz
```

Start everything:

```bash
docker compose up -d --build
```

Check it's running and healthy:

```bash
docker compose ps
docker compose logs -f m26     # watch startup; Ctrl+C to stop watching
```

The M26 log should show the security audit line and uvicorn starting. Caddy will
fetch the HTTPS certificate automatically (needs Step 4 done + ports open).

Quick check from your own computer:

```bash
curl https://books.m26technologies.co.sz/webhook
# "verification failed" is EXPECTED here — it means the app is up and answering.
```

## 9. Connect Meta's webhook

In Meta → M26's app → WhatsApp → Configuration → Webhook:
- **Callback URL:** `https://books.m26technologies.co.sz/webhook`
- **Verify token:** the exact `WHATSAPP_VERIFY_TOKEN` you set in `env/m26.env`
- Click **Verify and save** (Meta calls your server; it should succeed).
- **Subscribe** to the **messages** field.
- Important (this bit bit us before): make sure M26's **WABA is subscribed to
  *this* app** — under WhatsApp → API Setup / Configuration, confirm the app is
  subscribed, not only Meta's internal default app.

## 10. Test end-to-end

From an **approved** number (one in `OWNER_NUMBERS`), message M26's WhatsApp:
- Send "hi" → you get the welcome message.
- Send a photo of a receipt → it files it and replies.
- Ask "who owes me?" / "invoice X R500 for Y" → the agent responds; invoices come
  back as a PDF.
- From a number **not** in `OWNER_NUMBERS`, message it → you should get **no
  reply** (it's correctly ignored). Check `docker compose logs m26` to see the
  blocked attempt logged.

If all that works, M26 is live. 🎉

## 11. Turn on nightly backups

```bash
chmod +x /opt/bookkeeper/deploy/backup.sh
# Run once now to confirm it works:
/opt/bookkeeper/deploy/backup.sh
ls -lh /opt/bookkeeper/backups

# Schedule it every night at 02:30:
crontab -e
# add this line, save:
30 2 * * * /opt/bookkeeper/deploy/backup.sh >> /opt/bookkeeper/backups/backup.log 2>&1
```

Every so often, copy the newest file in `backups/` off the server (download it to
your PC) so a backup exists somewhere other than the server itself.

## 12. Optional: get alerted if it goes down

Sign up free at uptimerobot.com and add an HTTPS monitor for
`https://books.m26technologies.co.sz/webhook`. It'll email you if the bot ever
stops answering.

---

## Adding companies 2 and 3 later

For each new company (say "companyb"):
1. In `docker-compose.yml`, copy the `m26` service block, rename it `companyb`,
   and point its volumes at `./data/companyb/...`.
2. `cp env/m26.env.example env/companyb.env` and fill in **their** database name
   (`DB_NAME=bookkeeper_companyb`), WhatsApp number, business details, owners.
3. In `Caddyfile`, add their web address block (`books.companyb.co.za { reverse_proxy companyb:8000 }`).
4. Add the DNS A record for their address → your server IP.
5. `docker compose up -d --build`, then do Steps 9–10 for their number.

Same server, same cost — just another apartment in the building.

---

## Server management cheat sheet

Run these from `/opt/bookkeeper` on the server (`cd /opt/bookkeeper` first).

| I want to... | Command |
|---|---|
| See what's running | `docker compose ps` |
| Watch logs (all) | `docker compose logs -f` |
| Watch one company's logs | `docker compose logs -f m26` |
| Restart one company | `docker compose restart m26` |
| Restart everything | `docker compose restart` |
| Stop everything | `docker compose down` |
| Start everything | `docker compose up -d` |
| **Deploy a code update** | `git pull && docker compose up -d --build` |
| Change a company's settings | `nano env/m26.env` then `docker compose up -d m26` |
| Back up now | `deploy/backup.sh` |
| Free up disk from old images | `docker system prune -f` |

**Restore from a backup** (careful — this overwrites current data):

```bash
gunzip -c backups/all-databases-YYYYMMDD-HHMMSS.sql.gz \
  | docker compose exec -T db sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD"'
```

That's it. Day to day you won't touch it — the bots auto-restart on crash or
reboot, the OS patches itself, and backups run nightly.
