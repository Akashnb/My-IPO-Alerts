# IPO GMP Alert Agent — Setup Guide

This agent runs automatically every morning, checks currently **open Mainboard IPOs**
(SME excluded) using **two independent data sources cross-checked against each other**
(investorgain.com + ipowatch.in), and sends a **Telegram alert** for any IPO with
**GMP ≥ 10%**. It runs on GitHub's free servers — nothing needs to stay on on your
laptop or phone, and it can alert one chat or many (e.g. you + a friend's group).

**Why two sources:** if either site goes down, changes its layout, or a scraper
breaks, the other keeps the agent running. When both sources have data for the
same IPO, their GMP% is averaged and flagged in the alert if they disagree by
5 points or more, so you're not silently trusting one bad number. If *both*
sources ever fail on the same day, you'll get a warning message instead of
silence — so a break is visible immediately, not something you discover a week
later when you notice no alerts came through.

---

## Step 1 — Create your Telegram bot (5 min)

1. Open Telegram, search for **@BotFather**, tap **Start**.
2. Send `/newbot`, give it a name (e.g. `My IPO Alerts`) and a username ending in `bot`
   (e.g. `my_ipo_alerts_bot`).
3. BotFather replies with a **token** like `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.
   **Save this** — it's your `TELEGRAM_BOT_TOKEN`.

---

## Step 2 — Create the channel and add your bot

1. Telegram → menu → **New Channel** → give it a name (e.g. "Daily IPO GMP Alerts") →
   choose Public or Private → create.
   - If public, pick a username like `t.me/my_ipo_alerts` — that's the link you share with friends.
2. Open the channel → **Manage Channel → Administrators → Add Admin** → search for
   your bot's username → add it with at least "Post Messages" permission.
   A bot can't post to a channel unless it's an admin — this step is required.
3. Post any message in the channel yourself (e.g. "test") so Telegram has something
   to hand back in the next step.
4. Get the channel's **chat ID**: open this URL in a browser (replace `<TOKEN>`):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
   Look for `"chat":{"id":-100XXXXXXXXXX,...}` — note the `-100` prefix, that's the
   channel format. That number is your `TELEGRAM_CHAT_ID`.

---

## Step 3 — Create a GitHub repo

1. Go to [github.com](https://github.com) → sign up if you don't have an account (free).
2. Click **New repository**. Name it e.g. `ipo-gmp-agent`. Keep it **Public**
   (public repos get unlimited free GitHub Actions minutes; private repos get ~2,000 free min/month, which is still plenty for one daily run).
3. Upload the files from this project, keeping the folder structure exactly as-is:
   ```
   ipo-gmp-agent/
   ├── ipo_gmp_alert.py
   ├── requirements.txt
   └── .github/
       └── workflows/
           └── ipo_alert.yml
   ```
   Easiest way: on the repo page, click **Add file → Upload files**, drag all of them in
   (GitHub will preserve the `.github/workflows/` path if you drag the whole folder).

---

## Step 4 — Add your Telegram credentials as secrets

Never put your bot token directly in the code. Instead:

1. In your repo, go to **Settings → Secrets and variables → Actions**.
2. Click **New repository secret**:
   - Name: `TELEGRAM_BOT_TOKEN` → Value: (the token from Step 1)
3. Click **New repository secret** again:
   - Name: `TELEGRAM_CHAT_ID` → Value: (the channel chat ID from Step 2, starting with `-100`)

---

## Step 5 — Test it manually

1. In your repo, click the **Actions** tab.
2. Click **Daily IPO GMP Alert** on the left.
3. Click **Run workflow** (dropdown on the right) → **Run workflow**.
4. Wait ~1-2 minutes, click into the run to watch the logs.
5. Check your Telegram channel — if any mainboard IPO is open today with GMP ≥ 10%, you'll see a post.
   If none qualify, you'll see "No IPOs crossed the GMP threshold today" in the logs (no message sent, by design).

---

## Step 6 — Let it run automatically

That's it — nothing else to do. The workflow is scheduled for **2:30 AM UTC = 8:00 AM IST**
every day (`.github/workflows/ipo_alert.yml`). GitHub's cron can run a few minutes late
during busy periods; that's normal and not something you can control on the free tier.

To change the time: edit the `cron: "30 2 * * *"` line.
Format is `minute hour day month weekday`, always in **UTC**. Example: for 7:30 AM IST use `"0 2 * * *"`.

To change the GMP threshold: edit `GMP_THRESHOLD: "10"` in the same file.

---

## Sharing alerts with friends

Standard setup: **one Telegram Channel** (created in Step 2 above). You post
alerts to the channel; friends join via a single invite link; that's it.
  When a friend joins the channel, Telegram remembers them permanently on their
  servers. There's no database, no member list, and nothing here that can lose
  that data or need maintenance as the group grows — the bot only ever does one
  thing: post a message to the channel ID. Telegram fans it out to everyone in it.

## Important notes

- **GMP data is unofficial.** It comes from the grey market, not an exchange. Treat it as
  a sentiment signal, not investment advice — the agent just automates what you were
  already checking manually.
- **Site structure can change.** The script reads both sites by matching text
  patterns (IPO name, status, GMP %) rather than fixed column positions, which
  makes it fairly resistant to small layout tweaks. If a site redesigns significantly
  and the agent stops finding data on one of them, the other keeps it running while
  you fix it. To see what changed, run this locally:
  ```
  pip install -r requirements.txt
  playwright install chromium
  python ipo_gmp_alert.py --debug
  ```
  This prints every raw row scraped from both sources so you (or Claude) can quickly
  adjust the parsing logic.
- **Only Mainboard IPOs** are checked (SME excluded), per your request.
- **Free tier limits:** public GitHub repos get unlimited Actions minutes for this kind of
  scheduled job; this script takes well under a minute to run, so you're nowhere close to any limit even on a private repo.

## Optional upgrades (not required)

- **Also alert on "no IPOs today"** — uncomment the last line in `main()` in `ipo_gmp_alert.py`.
- **Real "AI" summarization** — pipe the day's IPO list through the Claude API to get a
  one-line plain-English take on each IPO (needs a paid Anthropic API key, so left out to
  keep this 100% free — happy to add it if you want it later).
