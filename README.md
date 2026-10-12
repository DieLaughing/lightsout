# Lights Out

**Every racing series in one place, and the drivers you follow, wherever they race.**

Lights Out is a small self-hosted web app for motorsport fans. It puts the schedules of F1, F2, F3, F1 Academy,
Formula E, IndyCar, NASCAR (Cup, O'Reilly, Truck), WEC, WRC, GT World Challenge Europe, the Nürburgring NLS, rallycross
and more into one calendar. Follow a driver by name and it watches every grid and entry list for them. That matters
when a driver hops between series: Max Verstappen turning up at a GT race, or a young driver doing rallies one month
and rally-raids the next.

It installs on a phone like an app (no app store), sends alerts, and you host it yourself for you and your friends.

> **A passion project.** Lights Out was built by one fan, and real use on race weekends shaped what came next.
> It's tested (`pytest`) and runs every day, but it's a hobby project, not a product. Expect rough edges, and feel
> free to fix them.

## What it does

- **Upcoming:** every session for the series you switch on, in your time zone, with **US where-to-watch** badges
  that make FREE vs PAID obvious.
- **Follows:** follow drivers or teams by name (spelling doesn't need to be perfect). A NEW badge appears when they're
  entered somewhere. Driver pages show where they race, their next sessions, championship positions and news.
- **Standings and results** for F1, F2, F3, F1 Academy, Formula E, IndyCar, WRC (and its classes) and NASCAR.
- **Live timing** for F1, F2, F3, F1 Academy, WRC, NASCAR and IndyCar, connected only while a session is on.
- **Race events:** penalties, safety cars, cautions with their cause, retirements and lead changes as they happen,
  plus a lap-by-lap **Race story** afterwards.
- **Race Wire:** racing news (Motorsport.com, Autosport, RACER, The Race, DirtFish and more) plus the big race
  moments, filterable by series, team or driver.
- **Alerts** (web push): race starts, cautions, your drivers' incidents and results, news about them.
- **Calendar feed:** subscribe once in Google/Apple/Outlook calendar and it stays updated.
- **Ticket links**, a **What's new** page, an offline page when the server is down, and an approval list so only
  people you approve get in.

## Not affiliated with anyone

Lights Out is an unofficial fan project. It is not affiliated with or endorsed by Formula 1, the FIA, NASCAR, IndyCar,
the ACO, WRC, SRO, the NLS or any team, broadcaster or news site. It reads the same public pages and feeds their own
websites use, politely (most sources once a day, hourly in race weeks). Those feeds can change or disappear at any time.
It shows no league or team logos. News items link to the original articles. Please don't run a public instance for
strangers. Run it for yourself and your friends.

## What you need

| | |
| --- | --- |
| **A computer that's always on** | Anything that runs Docker: a Raspberry Pi 4 or 5, a mini PC, a NAS, an old laptop, a small cloud server. Images are built for 64-bit ARM (Raspberry Pi OS 64-bit) and regular 64-bit PCs (x86-64). |
| **How much power** | Very little. On a Raspberry Pi 5 with 8 GB, Lights Out uses about **110 MB of RAM**, well under 1% CPU, and its data is a few MB. A 2 GB Pi is plenty, and it happily shares the box with Jellyfin, Home Assistant and so on. |
| **Docker** | [Docker Engine](https://docs.docker.com/engine/install/) with the Compose plugin (`docker compose`). On a Pi: `curl -fsSL https://get.docker.com \| sh`. |
| **To use it away from home** *(optional)* | A domain name (around $10/year) using Cloudflare for DNS, and a free Cloudflare account. Cloudflare Tunnel gets visitors to your box **without opening ports on your router**, and Cloudflare Access handles sign-in. |
| **Approval pushes** *(optional)* | The free [ntfy](https://ntfy.sh) app on your phone, so "Approve / Deny" arrives as a notification. |

## Quick start: on your home network (5 minutes)

Good for trying it out, or if only people on your Wi-Fi will use it. There's no sign-in, so **never expose this mode
to the internet.**

```sh
mkdir lightsout && cd lightsout
curl -O https://raw.githubusercontent.com/DieLaughing/lightsout/main/compose.yaml
curl -o .env https://raw.githubusercontent.com/DieLaughing/lightsout/main/.env.example
```

Edit `.env`: set `LIGHTSOUT_ADMIN_EMAIL` **and** `LIGHTSOUT_LOCAL_EMAIL` to your email, `LIGHTSOUT_BASE_URL` to
`http://<that computer's IP>:8787`, and `TZ` to your time zone.
To reach it from other devices on your network, change the port line in `compose.yaml` from `"127.0.0.1:8787:8787"`
to `"8787:8787"`. Then:

```sh
docker compose up -d
```

Open `http://<that computer's IP>:8787`. The first load takes a minute while it fetches every series. Phone alerts need
HTTPS, so for those use the Cloudflare setup below.

## Full setup: on the internet with Cloudflare (about 30 minutes)

This is how the original runs: people sign in with their email (a one-time code, no passwords), you approve who gets
in, and nothing on your home network is opened up. Below, replace `racing.example.com` with your own address.

**1. Put your domain on Cloudflare.** Create a free account at [cloudflare.com](https://dash.cloudflare.com/), add
your domain, and switch your registrar's nameservers to the two Cloudflare gives you. Wait until it says "Active".

**2. Turn on Zero Trust.** In the dashboard open **Zero Trust**. Pick a team name (it becomes
`<team>.cloudflareaccess.com`) and choose the **Free** plan (up to 50 users). Cloudflare may ask for a card even on
the free plan.

**3. Create a tunnel.** *Zero Trust → Networks → Tunnels → Create a tunnel → Cloudflared.* Name it, then copy the long
token from the install command (the part after `--token`). Put it in `.env` as `TUNNEL_TOKEN=`. You don't need to run
their install command, because the `cloudflared` service in `compose.yaml` does that job.
Then add a **Public hostname** to the tunnel:
- Subdomain + domain: `racing.example.com`
- Service: `HTTP`, URL `lightsout:8787` (the app's name inside Docker Compose)

**4. Lock it behind Cloudflare Access.** *Zero Trust → Access → Applications → Add an application → Self-hosted.*
- Application domain: `racing.example.com` (no path). Session duration: up to 1 month, so people rarely re-sign in.
- Policy: **Allow**, Include **Everyone**, login method **One-time PIN**. "Everyone" can *sign in*, but Lights Out
  keeps its own approval list, so only people you approve get past its "Request access" screen. (Or list your
  friends' emails here instead and approve them in the app too.)
- Save, then open the application's overview and copy the **Application Audience (AUD) Tag**.

In `.env` set:
```sh
LIGHTSOUT_BASE_URL=https://racing.example.com
LIGHTSOUT_ADMIN_EMAIL=you@example.com          # the email you'll sign in with
LIGHTSOUT_ACCESS_TEAM=<team>.cloudflareaccess.com
LIGHTSOUT_ACCESS_AUD=<the AUD tag>
LIGHTSOUT_LOCAL_EMAIL=                          # must stay empty on the internet
```

**5. Let a few paths skip sign-in.** Some things can't sign in: calendar apps, phones fetching home-screen icons, and
the Approve/Deny buttons in the ntfy app. Add a **second** self-hosted application (call it `lightsout-public-paths`)
with a **Bypass** policy (Include: Everyone) and these destinations:

| Path | Why it's safe |
| --- | --- |
| `racing.example.com/cal` | Calendar feeds: protected by a long secret token in each link |
| `racing.example.com/act` | ntfy Approve/Deny buttons: single-use signed links that expire in 24 h |
| `racing.example.com/icons` | App icons |
| `racing.example.com/manifest.webmanifest` | The "install as app" description |
| `racing.example.com/apple-touch-icon.png` | iPhone home-screen icon |
| `racing.example.com/apple-touch-icon-precomposed.png` | Older iPhone icon lookup |

**6. Start it.**
```sh
docker compose --profile tunnel up -d
```
Visit `https://racing.example.com`, sign in with your admin email, and you're in. Friends sign in with theirs, tap
**Request access**, and you approve them from the **Admin** tab, or straight from your phone (step 7). Admin can also
make **invite links** that let someone in without the request step.

**7. (Optional) Approve from your phone with ntfy.** Install the ntfy app and subscribe to a topic. On the public
server ntfy.sh, pick a **long random topic name**, since anyone who knows it can read the requests. Then set
`NTFY_URL=https://ntfy.sh` and `LIGHTSOUT_NTFY_TOPIC=<your topic>` in `.env`. If you self-host ntfy with access control,
also set `NTFY_TOKEN`. Run `docker compose up -d` again to apply.

### Already have a reverse proxy?

Lights Out signs people in with Cloudflare Access only (it checks Cloudflare's signed token). Without Cloudflare, use
home-network mode behind your own VPN (Tailscale, WireGuard). Support for other sign-in providers would be a welcome
contribution.

## On your phone

Open the site in **Safari** (iPhone) or **Chrome** (Android) and go to **Settings → Get the app**. It walks you
through adding it to the home screen and turning on alerts. On iPhone, alerts only work from the home-screen app.

## Settings

Everything is in `.env`. `.env.example` explains each line.

| Setting | What it does |
| --- | --- |
| `LIGHTSOUT_BASE_URL` | Your site's address, used in calendar and approval links |
| `LIGHTSOUT_ADMIN_EMAIL` | The admin: always let in, sees the Admin tab |
| `LIGHTSOUT_ACCESS_TEAM`, `LIGHTSOUT_ACCESS_AUD` | Cloudflare Access team domain and the app's AUD tag |
| `LIGHTSOUT_LOCAL_EMAIL` | Home-network mode: no sign-in, everyone is this person |
| `NTFY_URL`, `NTFY_TOKEN`, `LIGHTSOUT_NTFY_TOPIC` | Approval pushes (optional) |
| `TUNNEL_TOKEN` | Cloudflare Tunnel token (with `--profile tunnel`) |
| `TZ` | Server time zone (logs) |

What friends choose (series, follows, alerts, calendar) is saved per person in the app.

## Make it yours

The name, colors and icon are easy to change:

- **Name:** "Lights Out" appears in `web/index.html` (title and `apple-mobile-web-app-title`),
  `web/manifest.webmanifest` (`name`, `short_name`), `web/offline.html`, `web/sw.js` (notification title),
  `web/app.js` (a few sentences), `lightsout/access.py` and `lightsout/app.py` (push and ntfy messages),
  `lightsout/ics.py` (calendar name) and `tools/changelog.py`. To find them all:
  `grep -rn "Lights Out" web lightsout tools`.
- **Colors:** the CSS variables at the top of `web/style.css` (`--accent` and friends, light and dark), plus
  `theme_color`/`background_color` in the manifest.
- **Icon:** replace `web/icons/icon.svg`, then make the PNGs from it, for example
  `rsvg-convert -w 512 icon.svg > icon-512.png` (also 192 and 180). Bump the `?v=` number on the icon links in
  `web/index.html` and the manifest so phones fetch the new one.
- **Source link:** if you run a modified copy for other people, point
  `<meta name="source">` in `web/index.html` at your fork (see License).
- **Where-to-watch** is for the US. `lightsout/data/broadcasts.yaml` holds the rules if you want to adapt it.

After changing code, build your own image: in `compose.yaml` replace the `image:` line with `build: .` and run
`docker compose up -d --build`.

## Updating and backups

```sh
docker compose pull && docker compose up -d     # new version (published with each GitHub Release)
```
Everything that matters is in the `data/` folder next to `compose.yaml`: the database, your approved list, the
calendar token and the push-notification key. Back that folder up. If you lose the push key, everyone has to turn
alerts on again.

## Running from source (development)

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx
LIGHTSOUT_LOCAL_EMAIL=me@example.com LIGHTSOUT_ADMIN_EMAIL=me@example.com .venv/bin/uvicorn lightsout.app:app --port 8787
.venv/bin/python -m pytest -q
```
Data goes to `./data`. `LIGHTSOUT_NO_REFRESH=1` serves existing data without fetching. There's no build step: the web
app is plain HTML/CSS/JS in `web/`.

| Path | What |
| --- | --- |
| `lightsout/adapters/` | One module per source; each returns events and sessions in a common shape |
| `lightsout/live/` | Live timing feeds and the race-event classifier |
| `lightsout/data/*.yaml` | Hand-kept data: broadcasts, tickets, team aliases, manual schedules, What's new |
| `lightsout/refresh.py` | Refreshes each series daily, hourly in race weeks |
| `web/` | The installable web app |

## Contributing

Contributions are welcome: new series, fixes when a source changes its format, other countries' where-to-watch, other
sign-in providers. See [CONTRIBUTING.md](CONTRIBUTING.md). In short: **fork**, make your change on a branch, run the
tests, and open a pull request.

## License

[GNU AGPL v3.0 or later](LICENSE). In plain words: use it, change it, share it. If you run a **modified** version that
other people use over the internet, you have to offer them your source code (the "Source code" link on the What's new
page does that; point it at your fork). That way, improvements find their way back to everyone. Running it unmodified
for you and your friends needs nothing extra.
