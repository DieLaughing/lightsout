# Lights Out: notes for Claude

Self-hosted racing schedule app: every series in one place, follows that find drivers wherever they race, live timing,
a Race Wire, alerts, a calendar feed and US where-to-watch. Built with Claude Code; these notes are how it works.
Personal deployment notes (if any) live in CLAUDE.local.md, which is not committed.

## Shipping a feature (do this every time)
- Add an entry at the top of `lightsout/data/changes.yaml` (date, kind new|improved|fixed, title, text written for the
  people who use the app, link to try it). It feeds the What's new page (#/new), the dot on Settings and the home banner.
- `.venv/bin/python tools/changelog.py` rewrites CHANGELOG.md (a test fails if it's stale).
- `.venv/bin/python tools/release.py` creates/updates the day's GitHub Release (tag v<YYYY.MM.DD>) from the same
  entries; publishing a release builds the Docker image (.github/workflows/docker.yml).
- Tests: `.venv/bin/python -m pytest -q`. The follow-name matcher is web/match.js (node-testable).

## How it runs
- One container (Dockerfile), port 8787, all state in /data (SQLite, VAPID key, link secret, calendar token).
  Settings come from env vars: see .env.example. compose.yaml runs the published image (+ optional cloudflared).
- `./deploy.sh` (maintainers) rsyncs the source to a server over SSH, builds there and restarts; settings in deploy.env.
- `/` serves index.html with versioned asset URLs (Cloudflare caches .js otherwise). web/sw.js caches the shell and
  shows web/offline.html when the server can't be reached (Cloudflare 502/530).

## Speed and navigation (2026-10-13)
- Server: GZip on responses; gate() keeps finished, gzipped answers for READY_PATHS (same for everyone; /api/wire per
  person) for READY_TTL (30 s); /api/live, /api/events/, /api/story/ are never kept (they move during races). Requests
  over 0.5 s are logged ("slow request"). WAL + synchronous=NORMAL; the refresh loop (every 120 s) runs PASSIVE
  checkpoints on its own connection (Store.checkpoint) with automatic checkpoints off, so no request waits on one.
- Phone: api() keeps GET_CACHE; fast(path) waits up to FRESH_WAIT (150 ms) for fresh data, else draws the phone's copy
  and redraws once quietly (route(true): no animation) if it differs. prefetch() after startup and every 90 s (home
  sessions, default Wire, standings, entries, names). Startup loads settings, What's new and the series list at once;
  the home page uses the known follow status and refreshes it behind the scenes. showLoading() paints after 350 ms.
- Navigation: bottom .tabbar (#tabs; icons + labels; one .tab-pill moved by movePill()), Settings = gear .top-link,
  Admin is a link in Settings. Scrolling down folds the tab icons (.compact) and slides the header off (.tucked);
  any scroll up restores both. Slapshot page animation (Web Animations API, whole-pixel translate) when a navigation's
  content lands in #view; off with settings.animations = false or prefers-reduced-motion. backLink() has a chevron.

## Access
- Cloudflare Access signs people in (one-time PIN) and passes a signed JWT; lightsout/access.py verifies it against the
  team's certs (LIGHTSOUT_ACCESS_TEAM / _AUD). Anyone with an email gets that far; the app keeps its own approved list
  (user/invite tables). The admin (LIGHTSOUT_ADMIN_EMAIL) is always approved.
- "Request access" pushes to ntfy (optional) with Approve/Deny buttons = single-use signed links at /act. Invite links
  (/invite/<code>) work for up to max_uses people (invite_use table; a group link can't re-admit someone denied or
  revoked; closing a used link sets max_uses to its use count) and an /admin page.
- Paths that must bypass Access (separate Access apps with a Bypass policy): /act (ntfy buttons), /cal (calendar apps),
  /icons, /manifest.webmanifest, /apple-touch-icon.png, /apple-touch-icon-precomposed.png (phones fetch home-screen
  icons without the sign-in cookie).
- LIGHTSOUT_LOCAL_EMAIL (alias LIGHTSOUT_DEV_EMAIL) skips Cloudflare and treats every request as that person: local
  runs and home-network installs only.

## Features and where they live
- Follows are names only (no series): /api/follow-status searches every grid (entry table) and published entry list
  (event_entry: WRC via its results API, WEC/Le Mans via the ACO API at api-he.lemans.org with Origin
  https://www.24h-lemans.com). first_seen marks "NEW" entries (14 days); a series' first import is the baseline.
  WRC/ARA/ERX/W2RC are pick-your-events: registration never confirms a session, only an event entry list does.
- Settings (follows, series, calendar kinds, alert switches) live in user.settings; PUT /api/settings keeps only the
  keys sent. New series are switched on once for people with a saved series list (settings.seenSeries,
  web/app.js switchOnNewSeries).
- Standings + last race: F1, F2, F3, F1 Academy, IndyCar, Formula E, GTWCE (Cups/classes as a class picker), WRC (WRC2/WRC3/Junior/Masters as a class picker),
  NASCAR. IndyCar publishes no team championship. NASCAR's official results (weekend feed) can lag a day or more;
  until then Last race comes from the race's final timing feed (live/feeds/series_<n>/<race>/live_feed.json),
  labelled unofficial. Standings tab (#/standings/<series>/<drivers|teams|last>) and series pages share
  standingsBody(). Pages reached from elsewhere get backLink() (history.back when the previous page was in the app).
- Race Wire = news (lightsout/news.py: Motorsport.com per series, Autosport, RACER, The Race, DirtFish, NLS; every
  10 min) + important race events. Tagging in lightsout/names.py (grids + data/teams.yaml aliases; surnames only when
  unique in the series and not an everyday word).
- Race events: lightsout/live/events.py (EventLog, race control classifier). F1/F2/F3/F1A race control + retirements,
  leads (judged once per lap), pits, result; NASCAR flag history (caution reasons, free pass), lap notes (Race story
  only, except penalties), outs, leads, stages, result; IndyCar flags, DNFs with reason, leads; WRC stage winners,
  lead changes, penalties. Stored in race_event, linked to the schedule's event_key.
- Web push alerts (lightsout/alerts.py, VAPID key in the data dir): starts, cautions, followed drivers'
  incidents/penalties/outs/leads, results with your drivers' positions, news; never older than 15 min.
- Person pages (#/driver/<name>, #/team/<name>): /api/person/<name> = where they race (grid team + car number per
  series, events they're entered in), next qualifying/sprints/races across series (8), championship position per
  series, last race result, then their Wire items.
- Tickets: lightsout/data/tickets.yaml (series -> official tickets page). F1 race-specific pages (lightsout/tickets.py):
  f1_races in tickets.yaml (slug -> tickets.formula1.com/en/f1-<id>-<slug>), refreshed from the store twice a day when
  it isn't behind its Queue-Fair waiting room (never try to bypass it). Matched on race name, then venue (never
  country: Miami/Austin/Vegas). F2/F3/F1A use the GP page of the overlapping F1 weekend.

- Weather (lightsout/weather.py): places in lightsout/data/venues.yaml (first matching term on "<venue> <event name>"
  wins; rallies match on the rally name and use the service park town). Open-Meteo forecasts only for places with a
  session in the next 7 days, refreshed every 6 h (1 h when a session is < 36 h away), cached in /data/weather.json.
  Sessions get {c, rain, code} at the start hour or {hi, lo, rain, code} for untimed days; °C stored, user setting
  `units` (f/c). Unmatched venues are logged ("no weather place for ..."): add them to venues.yaml. W2RC skipped.
- Upcoming filters (web/app.js filterBar/passes): when / session type / racing type (Series.category) / free to watch,
  kept in localStorage. Series pages show season progress (seasonProgress) and official site/timing (Series.site/.timing).

- Event pages (#/event/<series>/<event_key>, /api/event/...): sessions with watch/tickets/weather, round N of M,
  entry list (event_entry joined with the grid for team/number; else the series grid), results when the standings'
  last_event is this event, Race story flag, other series at the same place (weather place, dates ±1 day) and news
  (series-tagged, -10/+4 days, mentioning a keyword from the name/venue/place terms minus GENERIC words).

- Bug reports (#/report, footer link on every page, Settings > Your reports): lightsout/bugs.py files anonymous GitHub
  issues (labels from-app + bug|enhancement; @mentions broken; page/device/version table) with LIGHTSOUT_GITHUB_TOKEN
  (fine-grained, Issues read/write); bug_report table keeps who sent what; /api/bugs refreshes state + the
  owner's latest comment every 10 min. 5 a day per person. No token = GitHub's prefilled new-issue link.

## Series notes
- GT World Challenge Europe (`gtwce`; not `gtwc` = FIA GT World Cup at Macau): lightsout/adapters/gtwce.py scrapes
  gt-world-challenge-europe.com: /calendar (upcoming cards + a separate "past events" list; cross-month dates repeat
  the year), /event/<id>/<slug> timetables (GMT column = UTC; Bronze Test dropped), /entry-list/<year>/<slug>.
  Endurance races 3 h, Spa 24 h, Sprint 1 h. Other SRO series use the same site template.
  Standings (gtwce.standings): /standings?filter_standing_type=<cup>_<class>_<drivers|teams> (cup 0 overall, 42 Endurance,
  43 Sprint; class 81 Gold, 82 Silver, 80 Bronze); every driver of a crew has a row, merged into "A / B" (crews()).
  Overall = drivers/teams, Endurance/Sprint/Gold/Silver/Bronze = classes. Last race = the newest round's last race
  classification (/results -> round page -> race-1|race-2|main-race); teams and name spellings for the crews come from
  the newest Sprint and Endurance classifications (standings write 'VAN DER LINDE').
- NLS (`nls`, lightsout/adapters/nls.py): WordPress /wp-json API. This year = page
  calendar-nurburgring-langstrecken-serie-<year> (one race per date; split at dates, "(2x4h)" beats "24h" in a name);
  next year until that page exists = the news post announcing the dates. No timetable or entry lists; drivers are
  spotted via the series' EN RSS feed in the Wire.

## Live timing
- lightsout/live/: hub connects 15 min before a session, drops 45 min after. F1 = SignalR Core at livetiming.formula1.com
  (no login). Test with an archived session: LIGHTSOUT_REPLAY_F1=<dir of .jsonStream files> LIGHTSOUT_REPLAY_SPEED=30
  LIGHTSOUT_REPLAY_SKIP=<seconds>; archives at livetiming.formula1.com/static/<Path from Index.json>.
- F2, F3, F1 Academy: ASP.NET SignalR hub "Streaming" (GetData2/JoinFeeds with series "F2"/"F3"/"F1 Academy");
  F2+F3 at ltss.fiaformula2.com/streaming, F1A at f2f3-prod-livetiming.azurewebsites.net/streaming. No tyres/teams.
- WRC: polls p-p.redbull.com/rb-wrccom-lintegration-yv-prod/api every 30 s on rally days (events/<id>.json, itineraries,
  rallies/<id>/entries|results.json, stages/<id>/stagetimes.json?rallyId=). Same API gives standings
  (season-detail, championship-detail, championship-overall-results). Test: LIGHTSOUT_WRC_EVENT=638 (Croatia 2026).
- NASCAR (cup / noaps / truck): cf.nascar.com/cacher — <year>/race_list_basic.json (schedule UTC + per-race TV network,
  stored in session.tv and mapped by the `tv:` table in broadcasts.yaml), <year>/<n>/points-feed.json, <year>/<n>/<race>/
  weekend-feed.json, live/live-feed.json (one feed for whichever series is on track; polled every 5 s). ARCA not in feed.
- IndyCar: live/indycar.py (indycar.blob.core.windows.net/racecontrol). Formula E live feed is unmapped.
- LIGHTSOUT_FORCE_LIVE=f2,f3 connects a feed outside its sessions (they return the last session's final state).
- The web app polls /api/live/<series> every 2 s (simpler than SSE through the Cloudflare tunnel).

## Sources
- Scraped: F1 (Jolpica), F2/F3 (round pages), F1 Academy (Next.js data), Formula E (calendar JSON-LD; session times TBA),
  IndyCar (public JSON feeds), WEC (round JSON-LD), WRC (calendar page), ARA (<year>-ara-schedule page),
  Euro RX (fia-rallycross.com/calendar).
- Hand-kept in lightsout/data/manual.yaml: Macau GT/FR/F4 World Cups, World Rally-Raid.
- US where-to-watch: lightsout/data/broadcasts.yaml; recheck each January.
- wrc.com and fiawec.com block non-browser requests, so every fetch impersonates Chrome (curl_cffi).
- wrc.com sometimes serves an empty page shell for event pages; use the calendar page instead.
- Refresh: lightsout/refresh.py, daily per series, hourly in race weeks, retries failures after 30 min. Be polite.

## Conventions
- Rallies: one calendar entry per day; rally-raids: one spanning entry. Calendar default: practice, qualifying, races.
- Where-to-watch badges must make PAID ONLY vs FREE (and the source) obvious.
- No league or team logos (trademarks); team marks are text.
