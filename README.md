# igft — Instagram follower tracker

`igft` is a local, single-user command-line tool for research. It keeps a record in PostgreSQL of
who follows a small set of Instagram profiles (up to about 10, each with fewer than about 10k
followers) and of when each follower first appeared. You run it by hand. It reads follower lists
slowly through a dedicated research account, and you annotate followers with your own tags and
marks in your SQL client.

## Before you use it

- **Terms of Service.** Instagram does not offer follower lists through its official APIs, and
  reading them this way breaks Instagram's Terms of Service. Use a dedicated research account,
  never your personal one. That account may be action-blocked or banned. The request-safety
  rules in igft lower the risk but cannot remove it.
- **Personal data.** igft stores personal data about third parties: the numeric Instagram ID,
  username, full name and private/verified flags that the follower list returns. It never stores
  profile picture URLs. Whoever runs igft is responsible for meeting the research-ethics and
  data-protection rules that apply to them, for example the GDPR: a lawful basis, data
  minimisation, secure storage and a retention plan.

## Installation and database setup

You need Python 3.12 or newer, [uv](https://docs.astral.sh/uv/) and a PostgreSQL server you can
create a database on.

1. Install the project and its dependencies. This creates `.venv/` and the `igft` command:

   ```
   uv sync
   ```

2. Create a PostgreSQL role and an empty database it owns. Run these as a PostgreSQL superuser
   (on Ubuntu, prefix each with `sudo -u postgres`; `psql` and `createdb` come with the
   `postgresql-client` package). Choose your own password:

   ```
   psql -d postgres -c "CREATE ROLE igft LOGIN PASSWORD 'change-me'"
   createdb --owner igft igft
   ```

3. Tell igft where the database is: either copy [`config.example.toml`](config.example.toml) to
   your config file and edit `database_url`, or set it in the environment:

   ```
   export IGFT_DATABASE_URL='postgresql+psycopg://igft:change-me@localhost/igft'
   ```

4. Create the tables:

   ```
   uv run igft db upgrade
   ```

   This runs the packaged migrations. It is safe to run again: when the schema is already
   current it says so and changes nothing. Run it again after updating igft, to apply new
   migrations.

`uv run igft --help` lists the commands. If you activate `.venv/`, you can run `igft` directly.
A database that cannot be reached, or a missing `database_url`, is reported as a one-line error
with exit code 1.

## Configuration

Settings are read from a TOML file, then overridden by environment variables named
`IGFT_<SETTING>` (for example `IGFT_DATABASE_URL`). The file is, in order of precedence:

1. the path given with `igft --config <path>`;
2. the path in `IGFT_CONFIG`;
3. the per-user config file: on Ubuntu `~/.config/igft/config.toml` (respects
   `XDG_CONFIG_HOME`), on macOS `~/Library/Application Support/igft/config.toml`.

A missing per-user file is fine (defaults apply); a missing file named by `--config` or
`IGFT_CONFIG` is an error. Unknown settings and invalid values are rejected at startup, before
any command runs. Start from [`config.example.toml`](config.example.toml).

| Setting | Default | Meaning |
|---|---|---|
| `database_url` | none (required for database commands) | PostgreSQL URL, e.g. `postgresql+psycopg://igft@localhost/igft` |
| `instaloader_session_path` | `<data dir>/igft/sessions/instaloader.session` | Saved instaloader session |
| `firefox_profile` | discovered | Firefox profile directory to import the instaloader session from |
| `instaloader_user_agent` | desktop Firefox for this OS | User agent sent with the imported Firefox cookies |
| `delay_min_seconds` | `15` | Minimum random delay between follower-page requests |
| `delay_max_seconds` | `45` | Maximum random delay between follower-page requests |
| `page_cap` | `300` | Maximum follower pages per `scan` or `resume` run |
| `early_stop_pages` | `2` | Pages in a row of already-known followers that end a default scan |
| `cooldown_hours` | `24` | Cooldown after a rate limit, action block or challenge |

`<data dir>` is the per-user data directory: on Ubuntu `~/.local/share` (respects
`XDG_DATA_HOME`), on macOS `~/Library/Application Support`. The config file and saved sessions
live outside the project directory; `config.toml` and `*.session` are git-ignored.

## The research account's session

igft never asks for the research account's password and never logs in. It reads the follower list
with a saved **session**: the cookies of a browser login you did yourself. `igft session import`
(see "Importing the session from Firefox") creates the session file, and `igft session check` tests
it.

- **Where it is.** The default path is `<data dir>/igft/sessions/instaloader.session`: on Ubuntu
  `~/.local/share/igft/sessions/instaloader.session` (respects `XDG_DATA_HOME`), on macOS
  `~/Library/Application Support/igft/sessions/instaloader.session`. Set `instaloader_session_path`
  in the config file or `IGFT_INSTALOADER_SESSION_PATH` to keep it somewhere else.
- **Permissions.** igft creates the sessions directory with mode `0700` and the session file with
  mode `0600`, because anyone who can read the file can act as the research account. It writes the
  file atomically: a crash during a save leaves the previous session untouched. When igft loads a
  file that other users can read or write (for example after a `chmod 644`), it prints a warning
  with the fix (`chmod 600 <path>`) and carries on. On Windows file modes are not checked.
- **A missing session.** Every command that needs Instagram checks the file first. Without it, the
  command exits with code 1 before any request, and tells you to log in to Instagram in Firefox and
  run `igft session import`.
- **`igft session check`.** Makes exactly one request to confirm the session still works and prints
  the account it belongs to. Every check, successful or not, is stored in the `session_checks`
  table. A failed check exits non-zero and tells you how to restore the session. The command is
  never refused during a cooldown or a challenge hold, and a successful check is what lifts a
  challenge hold (see "Request safety").

### Importing the session from Firefox

Log in to Instagram in Firefox with the research account, then run:

```
igft session import
```

igft copies Firefox's `cookies.sqlite` to a temporary file (Firefox keeps the original locked),
reads the `instagram.com` cookies from the copy, makes **one** request to check that the session
works, and only then saves it. It prints the username of the account the session belongs to. If
Firefox has no logged-in Instagram session, or the check fails, nothing is saved and an existing
session stays as it was. Running the command again replaces the saved session. It is never refused
during a cooldown or a challenge hold. Importing never asks for a password. Do the first login from
Firefox yourself, and when Instagram sends a "new login from a device you don't usually use"
notice, confirm "This was me" from the phone app **before** the first import.

**Which profile.** igft reads the default profile named in `profiles.ini`, looking in:

| Platform | Directory with `profiles.ini` |
|---|---|
| Ubuntu, Firefox from snap (the default) | `~/snap/firefox/common/.mozilla/firefox` |
| Linux, Flatpak | `~/.var/app/org.mozilla.firefox/.mozilla/firefox` |
| Linux, `.deb` or tarball | `~/.mozilla/firefox` |
| macOS | `~/Library/Application Support/Firefox` |
| Windows | `%APPDATA%\Mozilla\Firefox` |

If more than one of these holds a default profile, or none does, igft stops and lists what it
found. Set `firefox_profile` in the config file (or `IGFT_FIREFOX_PROFILE`) to the profile
directory itself, for example `~/snap/firefox/common/.mozilla/firefox/abcd1234.default`, and igft
uses that without looking any further. Find the directory name in Firefox at `about:profiles`.

**User agent.** Instagram can treat cookies presented with a different browser's user agent as
stolen. igft sends a desktop Firefox user agent built for your OS (on Ubuntu it contains
`X11; Ubuntu; Linux x86_64`), but its Firefox version number is fixed and may lag the browser you
use. For the closest match, open `about:support` in Firefox, copy the **User Agent** line and set it
as `instaloader_user_agent` in the config file (or `IGFT_INSTALOADER_USER_AGENT`).

**If Instagram changes its site.** Besides the cookies, igft makes two requests like Firefox's web
app: the profile page `GET /<username>/` (to read the numeric account ID) and the followers list
`GET /api/v1/friendships/<id>/followers/?count=12&search_surface=follow_list_page`. If they start
failing with no block signal, open the followers list of the target in Firefox with the Network
panel open (F12), compare that request's URL and headers with the ones in
`src/igft/fetchers/instaloader_adapter.py`, and update the differences.

## Targets

A **target** is a profile whose followers you track. A target is identified by its numeric
Instagram ID, so it stays the same target when the profile changes its username.

```
igft target add <username>
igft target list
```

- **`target add`** makes one request to look the profile up and stores its numeric ID and
  username. An unknown username is an error (exit code 1) and stores nothing. Adding a profile that
  is already a target creates no second one: igft says so, and if the username changed it updates
  the stored username and reports the rename. Like `scan`, it is refused during a cooldown or
  challenge hold, and a rate limit or other block signal on it starts a cooldown.
- **`target list`** shows each target's username, numeric ID, the date it was added, and the time
  and status of its latest scan (for example `complete (end of list)`, or `never scanned`). A scan
  that was stopped without a recorded reason, such as by a crash, shows as
  `unfinished (interrupted)`. It reads the database only and makes no Instagram request, so it
  works during a cooldown.
- **Private profiles.** igft does not check whether a target is private or whether the research
  account follows it, because the profile page does not say. If a target is private, follow it
  with the research account (and get the request accepted) *before* you add and scan it. If that
  stops being true, a scan stops with "the follower list came back empty" rather than recording
  anything.
- **No removal.** igft has no command that removes a target or its followers. If you must, do it
  in SQL yourself.

## Scanning

```
igft scan <username> [--full]
igft resume <username> [--restart]
```

A scan reads one target's follower list, one page (about 12 followers) per request, and saves
every page as it arrives together with the cursor for the next one. The target must have been
added with `target add`. It never makes a request per follower.

- **Baseline.** The first scan of a target is its baseline, including when you finish it with
  `resume`. It reads the whole list, and the followers it finds are never reported by
  `first-seen`: they were already following before igft started looking.
- **Early stop.** Later scans stop once `early_stop_pages` pages in a row (2 by default)
  contained only followers recorded by an earlier scan. This assumes the list comes roughly
  newest-first, which Instagram does not guarantee, so a follower that sits deep in the list can
  be missed. Run `igft scan <username> --full`, which reads the whole list, every so often (for
  example monthly) to catch those. Followers first stored during the current scan never count as
  known. The count starts again at zero when you `resume`, so a resumed scan may fetch a
  couple more pages before it stops.
- **Page cap.** A run fetches at most `page_cap` pages (300 by default). If the list is longer
  the scan stops unfinished and exits with code 5. With the default delay of 15 to 45 seconds a
  page takes 30 seconds on average, so a capped run takes about 2.5 hours, and a target with
  10,000 followers (about 834 pages) needs three runs for a baseline or `--full` scan. An early
  stopped scan usually needs only a few pages.
- **Resuming.** A scan that stops before the end, because of the page cap, Ctrl-C, an error, a
  block signal or a crash, stays *unfinished* and keeps every saved page. `igft scan` for that
  target is refused until it is finished, and points you to `igft resume <username>`, which
  continues the same scan from the saved cursor in the same mode (`--full` or not). With nothing
  to resume, it says so and makes no request.
- **`resume --restart`.** Starts again from page 1 and keeps the followers already saved. igft
  never does this by itself, and does not suggest it after a failed run: starting over from
  page 1 does not get past a restriction. A restarted baseline still reads the whole list.
- **An empty follower list.** A page with no followers is never treated as the end of the list.
  The scan stops unfinished with the stop reason `list_unavailable` and saves nothing for that
  page. What happens next depends on whether the scan had already saved followers:
  - *Nothing saved yet* (the first page of a new scan): this is how Instagram answers for a
    private target the research account does not follow. igft sets no cooldown and exits with
    code 5. Make the research account follow the target and run `igft resume <username>`.
  - *Followers already saved*, in this run or an earlier one: an account that could read the list
    a moment ago has most likely had it withheld, so igft treats it as a rate limit. It starts a
    cooldown and exits with code 4. (A target that turned private mid-scan looks the same and also
    costs a cooldown.) After the cooldown, open a followers list in Firefox with the research
    account, check that it is not empty, and run `igft resume <username>`.

  The end of a list is a page that has followers and no next cursor. A target that really has no
  followers cannot be scanned.
- **Repeats and misses.** Instagram's list can reorder between page requests, so a scan can see
  some followers twice and miss others (about 1 in 8 in the spike). igft does not try to correct
  this: a repeat is an ordinary update, and a missed follower is first seen by a later scan.
- **Progress.** After each saved page a scan prints one line to stderr, for example
  `Page 12 of at most 300 this run, 1,180 followers saved (34 new), next request in 27 s.` The
  page total is the page cap, an upper bound: the list may end, or an early stop may apply,
  sooner. igft does not know the follower total, so none is shown. The "new" count is left out
  for a baseline. The line for the page that ends the run is replaced by the summary below.
- **Summary and exit codes.** A finished scan prints its stop reason, the pages fetched and
  followers seen, and, except for a baseline, how many were seen for the first time. Exit codes
  are 0 for a finished scan, 1 for an unknown target, an unfinished scan or nothing to resume, 3
  when refused (see "Request safety"), 4 for a block signal (including a withheld follower list)
  and 5 for another error, Ctrl-C, an empty first page or the page cap. Every stop except 0 leaves the scan resumable.

## Reports: `list` and `first-seen`

```
igft list <username> [--format table|csv|json]
igft first-seen <username> [--since YYYY-MM-DD | --since-scan <scan-id>] [--format table|csv|json]
```

Both read only the database. They make no Instagram request and work while a cooldown is active
or no session is imported. `list` shows every follower igft has recorded for the target.
`first-seen` shows the followers that were first seen in the target's latest scan. With `--since`
it shows those first seen on or after that date, counted from local midnight, and with
`--since-scan` those first seen in any scan after the given scan ID (scan IDs are in the
`scans` table). The two options cannot be combined, and an unknown target exits with code 1.

**What "first seen" means.** It is the scan in which igft first recorded the follower. It is not
when they started following: that moment is not available from Instagram. A follower a scan
missed is first seen by a later scan, and the baseline's followers are never reported, because
they were already following before igft started looking. `first-seen` says so when the latest
scan is the baseline, when the target has never been scanned, and when the latest scan is not
complete. It prints these notes to stderr so that CSV and JSON on stdout stay valid.

Each row has the ID, username, full name, the time first seen, your tags and your mark. Times
are shown in your local time zone. The examples below are for two followers, `alice` (tagged
`lab` and `pilot`, marked) and `bob` (no full name, no tags, not marked):

`--format table` (the default; with no rows it prints `No followers to show.`):

```
┏━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━┓
┃   ID ┃ Username ┃ Full name ┃ First seen            ┃ Tags       ┃ Marked ┃
┡━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━┩
│ 1001 │ alice    │ Alice A.  │ 2026-09-15 12:30 CEST │ lab, pilot │ yes    │
│ 2002 │ bob      │           │ 2026-09-15 12:30 CEST │            │ no     │
└──────┴──────────┴───────────┴───────────────────────┴────────────┴────────┘
```

`--format csv` has a header row. Several tags are joined with `;`, and the mark is `true` or
`false`:

```
id,username,full_name,first_seen_at,tags,is_marked
1001,alice,Alice A.,2026-09-15 12:30 CEST,lab;pilot,true
2002,bob,,2026-09-15 12:30 CEST,,false
```

`--format json` is an array of objects, with the time in ISO 8601 with its UTC offset, the tags
as a list and the mark as a boolean. With no rows it is `[]`:

```
[
  {
    "id": 1001,
    "username": "alice",
    "full_name": "Alice A.",
    "first_seen_at": "2026-09-15T12:30:00+02:00",
    "tags": [
      "lab",
      "pilot"
    ],
    "is_marked": true
  },
  {
    "id": 2002,
    "username": "bob",
    "full_name": null,
    "first_seen_at": "2026-09-15T12:30:00+02:00",
    "tags": [],
    "is_marked": false
  }
]
```

**Opening the CSV in a spreadsheet.** Usernames and full names are chosen by the followers. A
value that starts with `=`, `+`, `-` or `@` can be read as a formula by Excel or LibreOffice.
igft writes the data unchanged, so import the file as text, or check the full name column, before
opening a file about followers you do not trust.

## Your tags and marks: the `persons` table

Every follower igft has seen is one row in the `persons` table, keyed by the numeric Instagram ID.
Two columns in that table are yours, not igft's: you edit them in your SQL client (`psql`, DBeaver,
DataGrip, and so on).

| Column | Type | Owner | Meaning |
|---|---|---|---|
| `id` | `bigint` | igft | Numeric Instagram ID. It stays the same when a username changes. |
| `username`, `full_name`, `is_private`, `is_verified` | text / boolean | igft | Latest values from the follower list. A later scan overwrites them, and the old username is not kept. A field the follower list did not return (a flag, or a missing or empty full name) keeps its stored value. |
| `tags` | `text[]` | **you** | Any number of free-form tags. Empty (`{}`) for a new person. |
| `is_marked` | `boolean` | **you** | A yes/no mark. `false` for a new person. |
| `first_stored_at`, `updated_at` | timestamptz | igft | When the person was first stored and last updated. |

igft never writes `tags` or `is_marked`: a scan that sees a tagged or marked person again leaves
both as you set them, and a test fails if any write statement in igft's source names either column.
Which target a person follows, and when igft first saw them, is in the `follows` table.

Changing tags and marks (these examples use the usernames `alice` and `bob`; use your own):

```sql
-- Add one tag to a person.
UPDATE persons SET tags = array_append(tags, 'lab') WHERE username = 'alice';

-- Set several tags at once.
UPDATE persons SET tags = '{lab,pilot}' WHERE username = 'bob';

-- Remove a tag.
UPDATE persons SET tags = array_remove(tags, 'pilot') WHERE username = 'bob';

-- Mark a person.
UPDATE persons SET is_marked = true WHERE username = 'alice';
```

Querying them:

```sql
-- Everyone with the tag 'lab'.
SELECT id, username, tags FROM persons WHERE 'lab' = ANY(tags);

-- Everyone with both tags.
SELECT id, username FROM persons WHERE tags @> '{lab,pilot}';

-- Everyone marked, with the target they follow and when igft first saw them.
SELECT t.username AS target, p.username, p.tags, f.first_seen_at
FROM follows f
JOIN persons p ON p.id = f.person_id
JOIN targets t ON t.id = f.target_id
WHERE p.is_marked
ORDER BY f.first_seen_at;
```

**Tagged and marked people cannot be deleted.** The database rejects a `DELETE` of a person who has
at least one tag or whose `is_marked` is true, and a `TRUNCATE persons` while any such person exists.
igft itself has no command that deletes a person. A person with no tags who is not marked can be
deleted, and their follow records go with them:

```sql
-- fails: person has tags or is marked, so the delete guard raises an error and nothing is deleted.
DELETE FROM persons WHERE username = 'alice';

-- Remove the tags and the mark first, and the delete goes through.
UPDATE persons SET tags = '{}', is_marked = false WHERE username = 'alice';
DELETE FROM persons WHERE username = 'alice';
```

`tags` cannot be set to `NULL`; use `'{}'` for no tags. The initial migration also refuses to be
downgraded, because that would drop these two columns.

## Request safety

igft is built to send as few requests as possible, slowly, and to stop at the first sign that
Instagram objects. Nothing here removes the risk described under "Before you use it".

- **Delays.** Between two follower-page requests igft waits a random time between
  `delay_min_seconds` and `delay_max_seconds` (15 to 45 seconds by default). The delay is measured
  from one request start to the next, so a slow database commit is not added on top. The first
  request of a run does not wait.
- **Page cap.** A `scan` or `resume` run fetches at most `page_cap` follower pages (300 by
  default). A run that reaches the cap stops and keeps its progress; `igft resume` continues it.
- **No retries.** Every request is made once. A failed or refused request is never repeated
  automatically.
- **One command at a time.** Commands that talk to Instagram take a PostgreSQL advisory lock. A
  second one started while the first runs is refused with "another igft command is using
  Instagram" (exit code 3).
- **Signals.** When Instagram answers with one of these, igft stops at once, saves the raw message
  and tells you what to do next:

  | Signal | What igft does |
  |---|---|
  | rate limit | Starts a cooldown. |
  | withheld follower list (Instagram redirects a follower request to its home page, or sends an empty page after the scan saved followers; it is recorded as a rate limit) | Starts a cooldown. Wait, then check in Firefox, logged in as the research account, that a followers list opens and is not empty, and then run `igft resume`. |
  | action block | Starts a cooldown. |
  | challenge (Instagram asks the account to verify itself, sometimes only to tick a "confirm you are human" box, with no email or SMS) | Starts a cooldown and a hold: see below. |
  | session rejected (the saved session is no longer valid) | Starts no cooldown. Restore the session with `igft session import`, then run `igft resume`. |

- **Cooldowns.** A cooldown lasts `cooldown_hours` (24 by default), or longer if the message
  states a longer wait (for example "wait 48 hours" gives 48 hours). A stated wait never makes the
  cooldown shorter than `cooldown_hours`. While a cooldown is active, `scan`, `resume` and
  `target add` are refused with the time it ends (exit code 3). Offline commands (`list`,
  `first-seen`, `target list`) and the `session` commands are never refused. Times are compared
  against the database clock.
- **How a challenge hold lifts.** After a challenge both conditions must hold: the cooldown has
  ended, and a `igft session check` that succeeded has run since the challenge started. Verify the
  account by hand first (for example by logging in to it in Firefox), restore the session if
  needed, then run `igft session check`. A check that fails, or that ran before the challenge, does
  not lift the hold.
- **Manual escape hatch.** Cooldowns are rows in the `cooldowns` table. If you are sure it is safe
  to continue, delete the row (`DELETE FROM cooldowns WHERE id = ...`). A challenge hold is lifted
  the same way. Doing this is on you: it is exactly the protection igft adds.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success. |
| 1 | User error: bad configuration, unknown target, missing session file, unreachable database. |
| 2 | Bad command line (from Typer). |
| 3 | Refused before any request: an active cooldown, a challenge hold, or another igft command holding the lock. |
| 4 | Instagram sent a block signal (rate limit, action block, challenge or rejected session). |
| 5 | Another fetch error, a follower list Instagram would not return, or a run stopped at `page_cap`. |
