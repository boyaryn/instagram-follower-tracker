# Spike findings

Throwaway spike for design.md D15. Run both scripts on the Ubuntu host against one small public
target (a few hundred followers). Each run appends a "Measured" block at the end of this file;
fill in the summary below from those blocks, then fold it back into design.md (task 2.4).

Do not deliberately provoke rate limits, action blocks or challenges.

## How to run

```bash
uv sync
# instaloader: Firefox (snap) logged in to Instagram with the research account.
# Copy the user agent from that Firefox (about:support, "User Agent").
uv run python spike/instaloader_spike.py --target <small_public_profile> --user-agent "<Firefox UA>"

# instagrapi (no longer a project dependency, design.md D5; run it with `uv run --with instagrapi==3.0.18`): asks for username, password and any 2FA/verification code once.
# Optionally match the device to your real phone, locale and time (applied only on a new login), e.g.
#   --device pixel-6a --android-release 17 --locale uk_UA --country-code 380 --timezone-offset 10800
uv run --with instagrapi==3.0.18 python spike/instagrapi_spike.py --target <small_public_profile>

# Optional, a day or more later: test whether an old cursor has expired.
uv run python spike/instaloader_spike.py --target <same> --user-agent "<UA>" --old-cursor-file spike/output/instaloader-cursor.txt
uv run --with instagrapi==3.0.18 python spike/instagrapi_spike.py --target <same> --old-cursor-file spike/output/instagrapi-cursor.txt
```

Sanitised responses are written to `tests/fixtures/<backend>/`. Raw logs, saved cursors and the
spike's instagrapi session stay in `spike/output/` (git-ignored, owner-only).

## Summary (fill in)

### instaloader

- Firefox profile found automatically with snap Firefox on Ubuntu: yes (run of 2026-10-01; path not recorded)
- Session check request (`test_login()` query `d6f4427f…`): one request, accepted (200, returned the research account)
- instaloader's own profile lookup (`api/v1/users/web_profile_info/`): 429 in 0.09 s with an empty body, on the
  run's second request. The web app does not send this request (see "Observed in Firefox" below), so the spike
  now reads the user id from the profile page instead.
- User id from the profile page (`GET /<target>/`, `"profile_id"` in the HTML): one request, 200, about 785k
  characters of HTML, exactly one distinct `profile_id`, matching the id Firefox showed.
- Follower list endpoint (`GET api/v1/friendships/<id>/followers/?count=12&search_surface=follow_list_page`, as the
  web app sends it): works through instaloader's session, 200, about 1 s per request. Page size returned for
  `count=12`: 12 on both pages (Firefox got 11 on its first page).
- Cursor format (`next_max_id`): a decimal offset as a string, `"12"` after page 1 and `"24"` after page 2, sent
  back as `max_id`. `has_more=true`, `big_list=true`, `page_size=12` on both pages.
- Garbled cursor response (`max_id=12x`): **not rejected**. 200 with `status: ok`, 12 users, 11 of them also on
  page 1, and `next_max_id: "12"`. Instagram appears to ignore an invalid cursor and serve the start of the list.
- **Order is not stable between requests**: page 2 (offset 12) shared 3 of its 12 users with page 1, and the
  garbled-cursor response shared 11 of 12 with page 1 rather than all 12. The list looks ranked
  (`follow_ranking_token`), and offsets into a shifting order give duplicates and, presumably, gaps. The web
  app passes nothing that would keep the order fixed: scrolling the Firefox popup sent exactly
  `?count=12&max_id=12&search_surface=follow_list_page`, then `max_id=24`, the same as the spike.
- **The web app sees the same shifts** (HAR of the Firefox popup, scrolled about 15 s per page, read with
  `spike/har_overlap.py`): pages at offsets 0, 12, 24, 36 returned 12 users each, 48 in total but 42 distinct.
  Page 2 repeated 2 users of page 1 and page 3 repeated 4 of page 2; every repeat came from the page just
  before. If the first 48 positions hold 48 different followers, about 6 of them (roughly 1 in 8) were never
  shown in one pass. Small sample, one target. Not tested: whether scrolling within 1–2 s avoids the shifts
  (the tracker's 15–45 s pacing rules that out anyway).
- Old cursor: not run. With offset cursors an old cursor is just an offset into today's order, so the test says
  little.
- Any redirect, extra request or hidden wait observed: none for the profile page and follower requests (sent
  directly, one request each). The session check still goes through instaloader's `get_json()`.

### instagrapi

- Login: **blocked on both attempts** (2026-10-01 and 2026-10-02, both about 19:45 UTC, 24 h apart, same result).
  instagrapi 3.0.16 with its default device profile (app 448.0.0.0.20, Pixel 8 Pro, Android 14) made 6
  requests: `graphql_www` 200, `bloks…process_client_data_and_redirect` 200, `attestation/create_android_keystore/`
  200, `bloks…oauth.token.fetch.async` 200, `GET qe/sync/` 405 (instagrapi's password-encryption key lookup), then
  `bloks…login.async.send_login_request/` **429 in 0.01 s, empty `text/plain` body** →
  `ClientThrottledError`. No checkpoint, 2FA or verification code was asked; the credentials were never
  evaluated. The 0.01 s empty reply looks like an edge rejection of the request, not a counted rate limit, and
  it matches the instant empty 429 instaloader got from `web_profile_info` on the same host.
- Third attempt (2026-10-02 21:28 UTC), changing everything that could be changed: a different account, a phone
  hotspot instead of the home network, instagrapi 3.0.18, and the device claimed as the owner's real phone
  (Pixel 6a, Android 17) with matching locale, calling code and timezone. **Same result**: the same 6 requests,
  the same 405 on `qe/sync/`, and `send_login_request` 429 in 0.08 s with an empty body. No prompt, no code.
  What stayed constant is instagrapi's login request itself: it reports the hardware keystore as unavailable in
  `X-IG-Attest-Params` (error -1013, no signed nonce), and it is sent by curl rather than the app's own network
  stack. Neither can be changed from a computer. Conclusion: instagrapi's mobile login is blocked; not retried.
- Session check, profile lookup, follower list, cursor format, garbled cursor, old cursor: **not reached** (no
  session).

### Consequences for the design

- Pages needed for a 10k-follower target per backend, and capped runs at 300 pages: instaloader (web endpoint,
  12 per page) about 834 pages, so 3 capped runs, roughly 7 hours of pacing at 30 s on average; instagrapi not
  measured, login blocked. Decided 2026-10-03: instagrapi is deferred to a later change (design.md D5), and there
  is no `--backend` option or `default_backend` setting in this change.
- `CursorExpired` classifier rule per backend: not needed. Decided 2026-10-02: the tool has no expired-cursor
  handling; a rejected cursor is an ordinary `FetchError`, and the user restarts with `resume --restart`. On the
  web endpoint an invalid cursor is not rejected at all (it silently serves the start of the list).
- Reordering between pages: accepted, not corrected (decided 2026-10-02, design.md Non-Goals). `new` was renamed
  `first-seen`, defined as "first recorded by the tool", with no claim about when someone started following.
- Early stop: kept as the default, although the list does not appear to be newest-first (design.md Risks).

## Observed in Firefox (2026-10-02)

Recorded by hand from Firefox's Network panel on the Ubuntu host, logged in as the research account, opening the
target's profile and then its followers popup (no scrolling).

- Loading the profile page does not call `api/v1/users/web_profile_info/`, which `Profile.from_username()` uses.
- The page document `GET /<target>/` contains the target's numeric user id in its HTML, under the keys `id`,
  `profile_id`, `target_id` and `container_id`; `profile_id` is the specific one.
- The profile data comes from `POST /api/graphql`, `fb_api_req_friendly_name=PolarisProfilePageContentQuery`,
  `doc_id=28036671149327607`, looked up by user id (`variables`: `id`, `enable_integrity_filters` and several
  `__relay_internal__pv__…` feature switches), status 200. `doc_id` and the switches are likely to change with
  web app releases, and the request body carries page tokens, so this request is not a good one to copy.
- The followers popup sends `GET /api/v1/friendships/<id>/followers/?count=12&search_surface=follow_list_page`,
  status 200: 11 users for `count=12`, `next_max_id: "12"` (an offset), other top-level keys `big_list`,
  `page_size`, `has_more`, `should_limit_list_of_followers`, `use_clickable_see_more`,
  `show_spam_follow_request_tab`, `follow_ranking_token`, `should_limit_list_of_followings`, `status`. This is
  the same endpoint instagrapi uses, on `www.instagram.com`, not the GraphQL `query_hash=37479f2b…` that
  `Profile.get_followers()` sends.

Consequence: both instaloader helpers the tracker needs (profile lookup and follower list) send requests the
current web app does not make. The spike now sends the web app's two requests through instaloader's cookie
session instead; the user id only has to be read once per target and can be stored.

## Measured blocks

### Measured: instaloader (2026-10-01 19:40 UTC)

- instaloader 4.15.3, page size requested: 12
- session check (test_login query): 1 HTTP request(s): GET https://www.instagram.com/graphql/query -> 200
- profile lookup (web_profile_info): 1 HTTP request(s): GET https://www.instagram.com/api/v1/users/web_profile_info/ -> 429
- profile lookup (web_profile_info) FAILED with ConnectionException: stopping (no retry)

### Measured: instagrapi (2026-10-01 19:45 UTC)

- instagrapi 3.0.16, page size requested: 50
- login FAILED with ClientThrottledError after 6 request(s)
- verification code prompted: no

### Measured: instaloader (2026-10-01 23:18 UTC)

- instaloader 4.15.3, page size requested: 12, profile and follower requests as the web app sends them
- session check (test_login query): 1 HTTP request(s): GET https://www.instagram.com/graphql/query -> 200
- profile page (GET /<target>/): 1 HTTP request(s): GET https://www.instagram.com/<target>/ -> 200
- profile page: 784946 characters of HTML, 1 distinct "profile_id" value(s)
- follower page 1: 1 HTTP request(s): GET https://www.instagram.com/api/v1/friendships/<user_id>/followers/ -> 200
- page 1: 12 followers, next_max_id len=2 digits prefix='12', big_list=True, page_size=12, has_more=True, should_limit_list_of_followers=False, use_clickable_see_more=False, show_spam_follow_request_tab=False, should_limit_list_of_followings=False; top-level keys: big_list, follow_ranking_token, has_more, next_max_id, page_size, should_limit_list_of_followers, should_limit_list_of_followings, show_spam_follow_request_tab, status, use_clickable_see_more, users; user fields: account_badges, fbid_v2, full_name, has_anonymous_profile_picture, id, is_private, is_verified, latest_reel_media, pk, pk_id, profile_pic_id, profile_pic_url, strong_id__, third_party_downloads_enabled, username
- follower page 2: 1 HTTP request(s): GET https://www.instagram.com/api/v1/friendships/<user_id>/followers/ -> 200
- page 2: 12 followers, next_max_id len=2 digits prefix='24', big_list=True, page_size=12, has_more=True, should_limit_list_of_followers=False, use_clickable_see_more=False, show_spam_follow_request_tab=False, should_limit_list_of_followings=False; top-level keys: big_list, follow_ranking_token, has_more, next_max_id, page_size, should_limit_list_of_followers, should_limit_list_of_followings, show_spam_follow_request_tab, status, use_clickable_see_more, users; user fields: account_badges, fbid_v2, full_name, has_anonymous_profile_picture, id, is_private, is_verified, latest_reel_media, pk, pk_id, profile_pic_id, profile_pic_url, strong_id__, third_party_downloads_enabled, username
- page 2 with a garbled cursor: 1 HTTP request(s): GET https://www.instagram.com/api/v1/friendships/<user_id>/followers/ -> 200
- garbled cursor response: 12 followers, next_max_id len=2 digits prefix='12', big_list=True, page_size=12, has_more=True, should_limit_list_of_followers=False, use_clickable_see_more=False, show_spam_follow_request_tab=False, should_limit_list_of_followings=False; top-level keys: big_list, follow_ranking_token, has_more, next_max_id, page_size, should_limit_list_of_followers, should_limit_list_of_followings, show_spam_follow_request_tab, status, use_clickable_see_more, users; user fields: account_badges, fbid_v2, full_name, has_anonymous_profile_picture, id, is_private, is_verified, latest_reel_media, pk, pk_id, profile_pic_id, profile_pic_url, strong_id__, third_party_downloads_enabled, username

### Measured: instagrapi (2026-10-02 19:45 UTC)

Reconstructed from `spike/output/instagrapi-raw-20261002T194537Z.json`; the script's own block did not reach this
file.

- instagrapi 3.0.16, page size requested: 50
- login FAILED with ClientThrottledError after 6 request(s); last request POST
  https://b.i.instagram.com/api/v1/bloks/async_action/com.bloks.www.bloks.caa.login.async.send_login_request/ -> 429
  (0.01 s, empty text/plain body)
- verification code prompted: no

### Measured: instagrapi (2026-10-02 21:28 UTC)

- instagrapi 3.0.18, page size requested: 50
- device locale settings overridden: device, android_release, locale, country_code, timezone_offset
- login FAILED with ClientThrottledError after 6 request(s)
- verification code prompted: no

### Measured: end of the follower list (2026-10-03, `spike/end_of_list_probe.py`, instaloader session, web endpoint)

Two small targets the research account could see, 12 followers requested per page.

- Target with 24 followers: page 1 = 12 followers, `next_max_id` len=2 digits prefix='12', `has_more` true,
  `big_list` true; page 2 = 12 followers, `next_max_id` absent, `has_more` false, `big_list` false, `page_size` 12.
  No trailing empty page.
- Target with 21 followers: page 1 as above; page 2 = 9 followers, `next_max_id` absent, `has_more` false,
  `big_list` false, `page_size` 9.
- The last page has followers, has no `next_max_id` key and has `has_more` false, whether or not the count is a
  multiple of the page size. Other top-level keys are the same as on earlier pages.
- A page can be shorter than `count` without being the last (the Firefox popup returned 11 for `count=12` with a
  cursor), so the end must come from the cursor and `has_more`, not from the page length.
