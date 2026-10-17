# Operating rules: Lights Out and Shots on Goal

Lights Out (racing) and Shots on Goal (NHL + PWHL) are sibling apps: separate repos, same design and the same rules.
This file is identical in both repos. Change it in both in the same session.

## Standards

- **Reviewed.** Every change in behaviour goes through `/code-review` and a cleanup pass (speed + cleanliness) before
  staging. Wording, typo and data-only changes (changes.yaml, notes) skip the review but still run the tests.
- **Fast.** The speed budget:
  - a tab visited before shows content in under 300 ms;
  - a first visit shows the app in under 1.5 s on a slow phone connection;
  - normal API requests answer in under 200 ms (95th percentile); 0.5 s stays the "slow request" log alarm.
- **Checked, not hoped.** `tools/perfcheck.py` runs every tab and API route through the 200 ms slow relay before
  staging. CI runs a page-speed audit on every release (local-email mode, no pollers, database from test fixtures);
  a failed budget blocks the image unless the release is overridden, with the reason in the release notes.
- **Watched in production.** Each app sends the admin a 9am ntfy summary of the past day's slow requests (top routes,
  worst times, tagged "during live" or "quiet"), only on days that had any.

## Shipping

- Behaviour changes: branch, then staging, then the owner's OK, then main and prod (tag the prod image first).
- Tests and tooling only: straight to main.
- A fix ported to the sibling app goes through the sibling's own staging if it changes behaviour.

## Shared parts

These exist in both apps in nearly the same form. When one changes, port it to the sibling in the same session. When
they have drifted, the newer and better-tested version wins.

| Part | Lights Out | Shots on Goal |
| --- | --- | --- |
| Access (Cloudflare JWT + approved list, invites, /act links) | `lightsout/access.py` | `sog/access.py` |
| Web push (VAPID) | `lightsout/push.py` | `sog/push.py` |
| In-app bug reports to GitHub | `lightsout/bugs.py` | `sog/bugs.py` |
| What's new | `lightsout/changes.py`, `tools/changelog.py`, `tools/release.py` | `sog/changes.py`, `tools/changelog.py`, `tools/release.py` |
| Calendar feeds | `lightsout/ics.py` | `sog/ics.py` |
| Service worker + offline page | `web/sw.js`, `web/offline.html` | `web/sw.js`, `web/offline.html` |
| Phone cache (`api()`, `fast()`, `prefetch()`, `showLoading()`) | `web/app.js` | `web/app.js` |
| Server answer caching, slow-request log, SQLite WAL + checkpoints | `lightsout/app.py`, `lightsout/db.py` | `sog/app.py`, `sog/feed.py`, `sog/db.py` |
| Page-view counts (anonymous, per page and day) | `lightsout/usage.py`, `countView()`/`sendViews()` in `web/app.js` | `sog/usage.py`, `countView()`/`sendViews()` in `web/app.js` |
| Deploy, Docker, CI | `deploy.sh`, `Dockerfile`, `.github/workflows/` | `deploy.sh`, `Dockerfile`, `.github/workflows/` |

New shared parts get a row here when they land (perfcheck, the slow-request summary).

## New ideas

- About one fun feature every two weeks, alternating between the apps. Big ones line up with season moments (season
  openers, deadlines, playoffs, finales). Bugs and requests from the app come first.
- Anything fun that lands in one app gets checked for a version in the sibling.
- Ideas live as GitHub issues labelled `idea`. Reports from the app arrive as `from-app` + `needs-triage`; triage turns
  them into an `idea`, a `ready-for-*` ticket or `wontfix`.
- A monthly idea session (prepared the Friday before the first Saturday, reminder Saturday 9am) drafts pitches from
  usage counts, the season calendar and requests. Only the ones the owner picks become issues.
- Other apps are inspiration only: study how they do things, never copy their data. Follow each app's terms; where
  they forbid scraping or automated browsing, work from screenshots and notes taken by hand.

## Domain

Both apps stay on their current hostnames. Push subscriptions, home-screen installs, calendar links and Access
apps are tied to the hostname, so moving would break them for everyone.
