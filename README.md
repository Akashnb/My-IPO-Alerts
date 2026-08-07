# 📈 IPO GMP Alert Agent

**Automatic daily Telegram alerts for high-GMP Mainboard IPOs — 100% free, runs on GitHub's servers**

---

> ### 🙋 Just want the alerts? No setup needed!
>
> If you simply want **daily updates on IPOs with GMP ≥ 10%** and don't want to
> set up anything yourself, just join my channel directly — it's already running:
>
> ## 👉 [**Join @my_ipo_alerts on Telegram**](https://t.me/my_ipo_alerts) 👈
>
> 🔔 No setup, no code, no GitHub account — just tap join and you're done.
>
> *Want to run your own copy instead (your own bot, your own channel, your own
> control)? Keep reading — the full setup guide is below.* ⬇️

---

## ✨ What this does

This agent runs automatically every morning, checks currently **open Mainboard IPOs**
(SME excluded) using **two independent data sources cross-checked against each other**
(investorgain.com + ipowatch.in), and sends a **Telegram alert** for any IPO with
**GMP ≥ 10%**. It runs on GitHub's free servers — nothing needs to stay on on your
laptop or phone, and it can alert one chat or many (e.g. you + a friend's group).

> **🔀 Why two sources?**
> If either site goes down, changes its layout, or a scraper breaks, the other keeps
> the agent running. When both sources have data for the same IPO, their GMP% is
> averaged and flagged in the alert if they disagree by 5 points or more, so you're
> never silently trusting one bad number. If *both* sources ever fail on the same day,
> you'll get a warning message instead of silence — so a break is visible immediately,
> not something you discover a week later when you notice no alerts came through.

---

## 🗺️ Setup roadmap

| Step | What you'll do |
|:---:|---|
| 1️⃣ | Create your Telegram bot |
| 2️⃣ | Create the channel and add your bot |
| 3️⃣ | Create a GitHub repo |
| 4️⃣ | Add your Telegram credentials as secrets |
| 5️⃣ | Test it manually |
| 6️⃣ | Let it run automatically |
| 7️⃣ | *(Recommended)* Add a free backup trigger |

---

## 1️⃣ Create your Telegram bot
*⏱ ~5 min*

1. Open Telegram, search for **@BotFather**, tap **Start**.
2. Send `/newbot`, give it a name (e.g. `My IPO Alerts`) and a username ending in `bot`
   (e.g. `my_ipo_alerts_bot`).
3. BotFather replies with a **token** like `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.

   > 🔑 **Save this** — it's your `TELEGRAM_BOT_TOKEN`.

---

## 2️⃣ Create the channel and add your bot

1. Telegram → menu → **New Channel** → give it a name (e.g. "Daily IPO GMP Alerts") →
   choose Public or Private → create.
   - If public, pick a username like `t.me/my_ipo_alerts` — that's the link you share with friends.
2. Open the channel → **Manage Channel → Administrators → Add Admin** → search for
   your bot's username → add it with at least "Post Messages" permission.

   > ⚠️ A bot can't post to a channel unless it's an admin — this step is required.

3. Post any message in the channel yourself (e.g. "test") so Telegram has something
   to hand back in the next step.
4. Get the channel's **chat ID**: open this URL in a browser (replace `<TOKEN>`):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
   Look for `"chat":{"id":-100XXXXXXXXXX,...}` — note the `-100` prefix, that's the
   channel format.

   > 🔑 That number is your `TELEGRAM_CHAT_ID`.

---

## 3️⃣ Create a GitHub repo

1. Go to [github.com](https://github.com) → sign up if you don't have an account (free).
2. Click **New repository**. Name it e.g. `ipo-gmp-agent`. Keep it **Public**.

   > 💡 Public repos get **unlimited free** GitHub Actions minutes; private repos get
   > ~2,000 free min/month, which is still plenty for one daily run.

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

## 4️⃣ Add your Telegram credentials as secrets

> 🔒 Never put your bot token directly in the code. Instead:

1. In your repo, go to **Settings → Secrets and variables → Actions**.
2. Click **New repository secret**:
   - Name: `TELEGRAM_BOT_TOKEN` → Value: (the token from Step 1️⃣)
3. Click **New repository secret** again:
   - Name: `TELEGRAM_CHAT_ID` → Value: (the channel chat ID from Step 2️⃣, starting with `-100`)

---

## 5️⃣ Test it manually

1. In your repo, click the **Actions** tab.
2. Click **Daily IPO GMP Alert** on the left.
3. Click **Run workflow** (dropdown on the right) → **Run workflow**.
4. Wait ~1–2 minutes, click into the run to watch the logs.
5. Check your Telegram channel:
   - ✅ If any mainboard IPO is open today with GMP ≥ 10%, you'll see a post.
   - ➖ If none qualify, you'll see "No IPOs crossed the GMP threshold today" in the
     logs (no message sent, by design).

---

## 6️⃣ Let it run automatically

That's it — nothing else to do for a basic setup. The workflow is scheduled in
`.github/workflows/ipo_alert.yml` via a `cron:` line, in **UTC**, format
`minute hour day month weekday`.

> ⚠️ **Heads up:** GitHub's own scheduler can run a few minutes late during busy
> periods, and — more importantly — can occasionally **skip a run entirely with no
> error and no notification**, especially at popular times like exact hours or
> half-hours. That's a known limitation of GitHub's free scheduler, not a bug in this
> repo. **Step 7️⃣** below shows how to add a free, independent backup so a missed
> GitHub-side trigger doesn't mean a missed alert.

- 🕒 **To change the time:** edit the `cron` line. Example: for 7:30 AM IST use
  `"0 2 * * *"` (remember IST is UTC+5:30, so subtract 5:30 from your target IST time
  to get the UTC value).
- 🎚️ **To change the GMP threshold:** edit `GMP_THRESHOLD: "10"` in the same file.

---

## 7️⃣ Add a free backup trigger `(recommended)`

GitHub's scheduler is best-effort — it can silently drop a run under load. This
repo's workflow already listens for a second trigger type, `repository_dispatch`,
specifically so you can "ping" it from an outside service as a safety net. If
GitHub's own schedule fires, great; if it doesn't, this backup fires the same
workflow instead. The workflow's built-in same-day dedup logic makes sure you
**never get two alerts** even if both triggers fire on the same day.

This uses **[cron-job.org](https://cron-job.org)**, a free external cron service —
no code changes needed. ✏️

### 7.1 — Create a GitHub Personal Access Token

1. GitHub → your profile picture (top right) → **Settings**
2. Left sidebar, scroll to the bottom → **Developer settings**
3. **Personal access tokens → Tokens (classic)**
4. **Generate new token → Generate new token (classic)**
5. Fill in:
   - **Note:** `cron-job-org-ipo-trigger`
   - **Expiration:** 90 days (or longer — you'll just need to repeat this step when it expires)
   - **Scopes:** ✅ check **repo** only (leave `workflow` unchecked — it's not needed for this)
6. Click **Generate token**.

   > 🔑 **Copy the token immediately** (starts with `ghp_`) — GitHub only shows it once.

### 7.2 — Create a free cron-job.org account

1. Go to [cron-job.org](https://cron-job.org) → **Sign up** → verify your email → log in.

### 7.3 — Create the cron job

1. Click **Cronjobs → Create cronjob**
2. **Title:** `IPO GMP Alert Trigger`
3. **Address (URL):**
   ```
   https://api.github.com/repos/OWNER/REPO/dispatches
   ```
   Replace `OWNER` and `REPO` with your actual GitHub username and repo name
   (visible in your repo's URL).
4. **Execution schedule:** select **Custom**, and enter your schedule as a
   crontab expression, a few minutes *before* your main GitHub `cron:` time so
   it acts as an early backup. Example: if your GitHub workflow runs at
   `"40 2 * * 1-5"` (8:10 AM IST, weekdays), set this to:
   ```
   35 2 * * 1-5
   ```
   > 📅 Weekday numbers: 1=Monday … 5=Friday. Check the **Next executions** panel
   > on the right to confirm it lists only weekdays at the right UTC time.

5. Go to the **Advanced** tab:
   - **Time zone:** leave as **UTC** ⚠️ (must match how you calculated the schedule above)
   - **Request method:** change to **POST**
   - **Request body:** paste exactly:
     ```json
     {"event_type": "ipo-alert-trigger"}
     ```
   - **Headers → + ADD**, three times, to add:

     | Name | Value |
     |---|---|
     | `Authorization` | `Bearer ghp_YOUR_TOKEN_HERE` |
     | `Accept` | `application/vnd.github+json` |
     | `Content-Type` | `application/json` |

     *(Use your real token from Step 7.1 in place of `ghp_YOUR_TOKEN_HERE`.)*

6. Save the job. 💾

### 7.4 — Test it now

1. On the job's page, click **▶ Run now**.
2. Check the job's execution history — you want a **204** status ✅ (success).
3. Go to your GitHub repo → **Actions** tab → you should see a new run appear,
   triggered via `repository_dispatch`.

> 🎉 Once that test run succeeds, you're done — this fires automatically every
> scheduled morning going forward, independent of GitHub's own scheduler.

> ℹ️ **Note:** your GitHub workflow's `.yml` file must already have a
> `repository_dispatch` trigger block listening for the same `event_type` you
> used above (`ipo-alert-trigger`) for this to work. This repo's `ipo_alert.yml`
> includes it already, so no code changes are needed — this step is purely
> external configuration.

---

## 👥 Sharing alerts with friends

Standard setup: **one Telegram Channel** (created in Step 2️⃣ above). You post
alerts to the channel; friends join via a single invite link; that's it.

> 💬 Don't want to run your own instance? Your friends can just join
> [**@my_ipo_alerts**](https://t.me/my_ipo_alerts) directly instead — no setup required.

> When a friend joins the channel, Telegram remembers them permanently on their
> servers. There's no database, no member list, and nothing here that can lose
> that data or need maintenance as the group grows — the bot only ever does one
> thing: post a message to the channel ID. Telegram fans it out to everyone in it.

---

## 📌 Important notes

- 💬 **GMP data is unofficial.** It comes from the grey market, not an exchange. Treat it as
  a sentiment signal, not investment advice — the agent just automates what you were
  already checking manually.
- 🧩 **Site structure can change.** The script reads both sites by matching text
  patterns (IPO name, status, GMP %) rather than fixed column positions, which
  makes it fairly resistant to small layout tweaks. If a site redesigns significantly
  and the agent stops finding data on one of them, the other keeps it running while
  you fix it. To see what changed, run this locally:
  ```bash
  pip install -r requirements.txt
  playwright install chromium
  python ipo_gmp_alert.py --debug
  ```
  This prints every raw row scraped from both sources so you (or Claude) can quickly
  adjust the parsing logic.
- 🏢 **Only Mainboard IPOs** are checked (SME excluded), per your request.
- 💰 **Free tier limits:** public GitHub repos get unlimited Actions minutes for this kind of
  scheduled job; this script takes well under a minute to run, so you're nowhere close to any limit even on a private repo.
- ⏰ **GitHub's scheduler is best-effort.** It can be delayed or occasionally skip a
  scheduled run entirely under load, with no error shown anywhere. Step 7️⃣'s backup
  trigger is the fix for this — free, and requires no code changes.

---

## 🚀 Optional upgrades `(not required)`

- 🔕➡️🔔 **Also alert on "no IPOs today"** — uncomment the last line in `main()` in `ipo_gmp_alert.py`.
- 🤖 **Real "AI" summarization** — pipe the day's IPO list through the Claude API to get a
  one-line plain-English take on each IPO (needs a paid Anthropic API key, so left out to
  keep this 100% free — happy to add it if you want it later).

---

*Built to run quietly in the background, forever, for free. ☕*
