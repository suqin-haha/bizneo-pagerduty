# Bizneo on-call hours

Python CLI that turns an on-call time window into Bizneo employee time requests
for **non-working hours only** (default weekday work: `08:00-17:00`).

The time logic stays simple:
- weekdays: submit only before `08:00` and after `17:00`
- weekends: submit the full on-call overlap
- report first, then manual confirm, then submit

## Setup

```bash
uv venv --python /usr/bin/python3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
uv run playwright install chromium
source .venv/bin/activate
cp .env.example .env
```

If `uv venv .venv` fails with `Unknown operating system`, pass a system
Python explicitly (`--python /usr/bin/python3.12`). Some uv-downloaded
interpreters do that on this host.

Edit `.env`:

```env
BIZNEO_BASE_URL=https://example.bizneohr.com
BIZNEO_SESSION_FILE=.bizneo-session.json
PAGERDUTY_CALENDAR_URL=https://example.pagerduty.com/private/<token>/feed/<schedule>
PAGERDUTY_TIMEZONE=Europe/London
PAGERDUTY_SUMMARY_CONTAINS=Your Name
```

`PAGERDUTY_CALENDAR_URL` is a private WebCal/ICS feed (`webcal://` is accepted).
Treat it like a password. PagerDuty feeds keep about **one month of history**
and up to **six months ahead**.

`PAGERDUTY_SUMMARY_CONTAINS` (or `PAGERDUTY_ATTENDEE`) keeps only your shifts
when the schedule feed includes everyone.

Employee id comes from login. Projects are scraped from the Bizneo request page.

## Login once

Authentication is handled like `python-bizneo`: open a real browser, log in,
save the session, then reuse it later.

```bash
uv run python -m bizneo_oncall login
```

You do **not** copy a CSRF token. The script fetches a fresh one from the Bizneo
request page before every submit.

If the Bizneo session expires, just run `login` again.

## Submit requests

Pick a month from the PagerDuty calendar (default path):

```bash
python -m bizneo_oncall submit --last-month --mode all
```

Or pick a specific month:

```bash
python -m bizneo_oncall submit \
  --month 2026-08 \
  --mode all
```

Each shift gets its own default description from the calendar, for example:

```
On-Call Team
Level 1
Sep 11, 3:00pm - Sep 18, 3:00pm(1 week)
```

Days in the same shift share that text. A later shift in the same month
gets its own date range. Pass `--description` to override every shift.

Only time that has **already ended** is requested. A future month, or the
unfinished part of the current month, is skipped with a message (no report).

If both `--month` and `--last-month` are omitted, the script lists months
found in the feed and asks you to choose.

Manual window (no calendar fetch). `--description` is optional here too;
without it the script generates one from the range:

```bash
python -m bizneo_oncall submit \
  --range "Jul 31, 3:00pm - Aug 7, 3:00pm" \
  --year 2026 \
  --mode all
```

Interactive prompts are used when `--mode`, `--project`, `--month`, and
`--last-month` are omitted. Description is generated unless you pass
`--description`.

When there is something to submit, the script prints a **colored
before/after report**, then asks for confirmation. `--yes` skips that
prompt.

## Modes

| Mode | Behavior |
|------|----------|
| `all` | Weekdays: submit before `08:00` and after `17:00`. Weekends: full on-call day. |
| `weekends` | Only Saturday/Sunday (full on-call intersection). |

## Example

On-call `Jul 31, 3:00pm - Aug 7, 3:00pm`, work `08:00-17:00`:

- Jul 31: `17:00-23:59` (15:00-17:00 already covered by work)
- middle weekdays: `00:00-08:00` and `17:00-23:59`
- weekends: `00:00-23:59`
- Aug 7: `00:00-08:00`

## Tests

```bash
uv run pytest -q
```

## How auth works

- `login` opens Bizneo in Chromium
- you log in normally, including SSO/MFA if needed
- Playwright stores the browser session in `.bizneo-session.json`
  (requires `playwright install chromium` once)
- later, submit commands reuse those cookies
- the script fetches a fresh `_csrf_token` from
  `/time-attendance/logged-time-requests/new` before POSTing the form
