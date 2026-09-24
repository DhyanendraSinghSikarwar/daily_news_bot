# Daily AI News Digest

A free bot that sends you a daily list of AI and data science headlines on **Telegram** and by **email**.

Every morning a GitHub Actions job fetches articles published in the last 24 hours from about 20 sources:

- **AI labs:** OpenAI, Anthropic, Google DeepMind, Google Research, Meta, Hugging Face
- **News:** MIT Technology Review, The Verge, VentureBeat
- **Research:** Hugging Face Daily Papers
- **Discussions:** Hacker News, Reddit
- **Blogs:** Towards Data Science, MarkTechPost, KDnuggets, Analytics Vidhya, Medium

The job removes duplicates and sends you the article titles as clickable links, grouped by source.

- **Free.** No paid APIs, no server, and no API keys are needed to fetch news.
- **Automatic.** After the one-time setup below, it runs every day with no action from you.
- **Resilient.** If one source fails, the rest still arrive. If Telegram fails, the email is still sent, and the other way round.

## What's in this repository

| File | Purpose |
| --- | --- |
| `news_bot.py` | The bot: fetches, deduplicates, formats and sends. The list of sources is at the top. |
| `requirements.txt` | The two Python libraries it needs, pinned to exact versions. |
| `.github/workflows/daily.yml` | The GitHub Actions schedule that runs the bot every day. |
| `last_run.txt` | Created automatically. Its daily update keeps GitHub from switching off the schedule. |

---

## Setup (about 15 minutes)

You need three things: a Telegram bot, a Gmail App Password, and a GitHub repository. You can skip either channel. The bot only uses the channels whose secrets you add.

### Step 1: Create a Telegram bot and find your chat ID

1. In the Telegram app, search for **@BotFather** (it has a blue verified tick) and open the chat.
2. Send `/newbot`. Choose a display name (for example `My AI News`), then a username that ends in `bot` (for example `my_ai_news_digest_bot`).
3. BotFather replies with a **token** that looks like `8123456789:AAH...xyz`. This is your **`TG_TOKEN`**. Keep it private.
4. Open a chat with **your new bot** (tap the `t.me/...` link BotFather gave you) and press **Start**, or send it any message such as `hi`. A bot can't message you until you've messaged it first.
5. In a browser, open this address, replacing `<TOKEN>` with your token:

   ```text
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```

6. In the text that appears, find `"chat":{"id":123456789,...`. That number is your **`TG_CHAT_ID`**.
   - If you see `"result":[]`, send your bot another message and refresh the page.
   - For a group, add the bot to the group, send a message there, and use the group's id. Group ids are negative, like `-1001234567890`. Include the minus sign.

### Step 2: Create a Gmail App Password

Gmail won't accept your normal password from a script. It needs an **App Password**, which Google only offers once 2-Step Verification is on.

1. Go to <https://myaccount.google.com/security> and turn on **2-Step Verification** if it's off.
2. Go to <https://myaccount.google.com/apppasswords>. You may need to sign in again.
3. Type a name such as `AI News Bot` and click **Create**.
4. Google shows a 16-letter password like `abcd efgh ijkl mnop`. Copy it now, because you can't view it again. This is your **`GMAIL_APP_PASSWORD`**. The spaces are optional.
5. Your Gmail address (for example `you@gmail.com`) is your **`GMAIL_USER`**.

> Using a Google Workspace or school account? Your administrator may have disabled App Passwords. In that case, a personal `@gmail.com` account is the easiest option.

### Step 3: Create the GitHub repository and upload the files

1. Sign in at <https://github.com> (a free account is fine) and click **New repository**, or go to <https://github.com/new>.
2. Give it a name such as `ai-news-digest`. You can choose **Private** or **Public**; both are free. Click **Create repository**.
3. On the new repository's page, click **uploading an existing file**.
4. Drag in `news_bot.py`, `requirements.txt` and `README.md`, then click **Commit changes**.
5. Add the workflow file. The browser upload can drop the hidden `.github` folder, so create this file by hand:
   1. Click **Add file → Create new file**.
   2. In the name box, type `.github/workflows/daily.yml` exactly. The slashes create the folders.
   3. Paste in the contents of `daily.yml` and click **Commit changes**.

> If you use git, you can instead run `git init`, `git add .`, `git commit -m "Initial commit"`, then `git remote add origin <repo-url>` and `git push -u origin main`.

### Step 4: Add your secrets

In the repository, go to **Settings → Secrets and variables → Actions → New repository secret**. Add each secret below one at a time. Names are case-sensitive.

| Secret name | Value | Required? |
| --- | --- | --- |
| `TG_TOKEN` | The bot token from BotFather | For Telegram |
| `TG_CHAT_ID` | Your chat id from `getUpdates` | For Telegram |
| `GMAIL_USER` | Your Gmail address | For email |
| `GMAIL_APP_PASSWORD` | The 16-letter App Password | For email |
| `MAIL_TO` | The recipient address. Separate several with commas, e.g. `me@gmail.com, friend@example.com` | Optional. Defaults to `GMAIL_USER` |
| `LOOKBACK_HOURS` | How many hours back to include articles | Optional. Default `24` |
| `MAX_PER_SOURCE` | The maximum number of articles from each source | Optional. Default `5` |

### Step 5: Run it once to test

1. Open the **Actions** tab. If GitHub asks, click **I understand my workflows, go ahead and enable them**.
2. Click **Daily AI News Digest** on the left, then **Run workflow → Run workflow**.
3. After a minute or two the run shows a green tick. You should now have a Telegram message and an email. The email may land in your Promotions or Spam folder the first time. Mark it **Not spam**.
4. Click the run, then **Fetch news and send digest**, to see the log. It ends with a report on each source, for example:

   ```text
   Source report:
     OpenAI News                    6 recent,  5 kept
     r/LocalLLaMA                 FAILED  HTTPError: 403 Client Error ...
   21/22 sources fetched, 65 articles in digest
   ```

From now on the digest arrives by itself every day.

---

## Customising

### Add or remove sources

Open `news_bot.py` and edit the `SOURCES` dictionary near the top. It has one section heading per group, with the source name and feed URL inside:

```python
SOURCES = {
    "News": {
        "The Verge (AI)": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
        "Ars Technica (AI)": "https://arstechnica.com/ai/feed/",   # <- a new source
    },
    ...
}
```

- **To add a source,** add a line `"Name": "feed URL",`. Any RSS or Atom feed works. To find a site's feed, try adding `/feed`, `/rss` or `/rss.xml` to its address, or search for "*site name* RSS".
- **To remove a source,** delete its line, or put `#` at the start to switch it off.
- **Order matters.** When two sources carry the same story, the one higher up the list keeps it.
- **Other examples:** a Medium tag is `https://medium.com/feed/tag/<tag>`, a subreddit is `https://www.reddit.com/r/<name>/top/.rss?t=day`, and a Hacker News keyword search is `https://hnrss.org/newest?points=100&q=<words>`.

To test your changes on your own computer, see [Running locally](#running-locally).

### Change the delivery time

Edit the `cron:` line in `.github/workflows/daily.yml`. **The time is in UTC.** The default, `"30 2 * * *"`, means 02:30 UTC, which is 08:00 in India.

| You want | Cron line |
| --- | --- |
| 07:00 IST | `"30 1 * * *"` |
| 09:00 UTC / 10:00 UK summer time | `"0 9 * * *"` |
| 08:00 US Eastern (summer time) | `"0 12 * * *"` |
| Weekdays only at 06:00 UTC | `"0 6 * * 1-5"` |

<https://crontab.guru> explains any cron line in plain English. GitHub often starts scheduled runs 5 to 30 minutes late at busy times, which is normal.

### Change how much you get

Add the `MAX_PER_SOURCE` or `LOOKBACK_HOURS` secrets. For example, `MAX_PER_SOURCE=3` gives a shorter digest. Setting `LOOKBACK_HOURS=168` together with a weekly cron such as `"0 6 * * 1"` gives a weekly digest.

---

## Running locally

```bash
pip install -r requirements.txt
python news_bot.py --dry-run      # fetches everything, prints the digest, sends nothing
```

To test real delivery from your computer, set the same variables first.

```bash
# macOS / Linux
export TG_TOKEN="..." TG_CHAT_ID="..." GMAIL_USER="..." GMAIL_APP_PASSWORD="..."
python news_bot.py
```

```powershell
# Windows PowerShell
$env:TG_TOKEN="..."; $env:TG_CHAT_ID="..."; $env:GMAIL_USER="..."; $env:GMAIL_APP_PASSWORD="..."
python news_bot.py
```

If a channel's variables aren't set, that channel is skipped with a log message.

---

## Troubleshooting

In GitHub, open **Actions → the failed run → Fetch news and send digest** to read the log. Delivery errors are on lines starting with `ERROR`, and the source report is just above them.

### Telegram

| Error in log | Cause and fix |
| --- | --- |
| `HTTP 401: Unauthorized` | The token is wrong. Copy it again from BotFather (`/mybots → your bot → API Token`) into the `TG_TOKEN` secret, with no spaces or quotes. If you regenerated the token, the old one stopped working. |
| `HTTP 400: Bad Request: chat not found` | `TG_CHAT_ID` is wrong, or you never pressed **Start** in the bot's chat. Message the bot, open `getUpdates` again, and copy the number, including any minus sign. |
| `HTTP 403: Forbidden: bot was blocked by the user` | You blocked or deleted the bot chat. Open the bot and press **Start** or **Restart**. |
| `HTTP 403: Forbidden: bot is not a member of the group chat` | Add the bot to the group again. |
| `HTTP 400: can't parse entities` | Please report it, because titles should always be escaped. As a workaround, remove the source whose title caused it. |

### Email (Gmail SMTP)

| Error in log | Cause and fix |
| --- | --- |
| `(535, b'5.7.8 Username and Password not accepted')` | You used your normal password, or the App Password has a typo. Create a new App Password (Step 2) and update `GMAIL_APP_PASSWORD`. Check that `GMAIL_USER` is the same account that created it. |
| `(534, b'5.7.9 Application-specific password required')` | 2-Step Verification is on, but you used your normal password. Use an App Password instead. |
| The App Passwords page says "not available" | 2-Step Verification is off, or your Workspace admin blocks App Passwords. Turn on 2-Step Verification, or use a personal Gmail account. |
| The run is green but no email arrived | Check Spam and Promotions, and check `MAIL_TO` for typos. If it keeps going to spam, add `GMAIL_USER` to your contacts. |

### Feeds and content

| Symptom | Cause and fix |
| --- | --- |
| Reddit shows `FAILED ... 403` or `429` | Reddit often blocks requests from GitHub's servers. The bot logs it and carries on, so the rest of the digest still arrives. If it happens every day, remove the Reddit lines. Hacker News still covers what people are discussing. |
| A source shows `0 recent` | That site simply published nothing in the last 24 hours. Labs such as Anthropic and Google Research don't post every day. This isn't an error. |
| A source shows `FAILED ... 404` or `could not parse feed` | The feed URL has changed or the site dropped its feed. Find its new feed URL, or delete the line. |
| "No new articles today" every day | Most likely every source failed. Read the source report in the log. If they all show `ConnectionError`, try again later, since GitHub's network sometimes has a bad hour. |
| Medium items look spammy | Medium tags are open to anyone. Remove the Medium lines, or keep just one tag. |

### GitHub Actions

| Symptom | Cause and fix |
| --- | --- |
| No **Run workflow** button | The workflow file isn't at exactly `.github/workflows/daily.yml`, or the Actions tab hasn't been enabled yet (Step 5.1). |
| Keep-alive step fails with `Permission denied` or `403` | Go to **Settings → Actions → General → Workflow permissions** and choose **Read and write permissions**. |
| The schedule stopped running | GitHub switches off schedules in public repos after 60 days without activity. The daily `last_run.txt` commit prevents this, but if it has happened, open **Actions**, choose the workflow, and click **Enable workflow**. |

---

## About the sources

- **Anthropic** publishes no RSS feed. The bot uses a well-known community mirror of `anthropic.com/news` ([Olshansk/rss-feeds](https://github.com/Olshansk/rss-feeds)), which is regenerated every hour.
- **Meta AI's blog** (`ai.meta.com/blog`) has no feed, so the bot uses the AI tag of Meta's official newsroom, `about.fb.com`.
- **VentureBeat's** AI-only feed has stopped updating. The main VentureBeat feed, which is mostly AI stories, is used instead.
- **Papers with Code** shut down in 2025 and now redirects to Hugging Face. The official Hugging Face Daily Papers API is used instead, and its papers are ranked by community upvotes.
- **Hacker News** uses [hnrss.org](https://hnrss.org), a long-running free feed service. It covers stories with 100 or more points that match AI and LLM keywords.
