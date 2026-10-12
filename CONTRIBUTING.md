# Contributing

Thanks for wanting to help! This is a hobby project by a fan, so the bar is "makes it better
for people who use it", not perfection.

## How to send a change

1. **Fork** this repository on GitHub (the Fork button), and clone your fork.
2. Make a branch: `git switch -c fix-indycar-standings`.
3. Set up and run the tests:
   ```sh
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx
   .venv/bin/python -m pytest -q
   ```
4. Make your change. Run the app locally with the commands in the README ("Running from source").
5. If people using the app would notice the change, add an entry at the top of ``lightsout/data/changes.yaml``
   (date, kind `new` / `improved` / `fixed`, a title, one or two plain sentences, and a link to try it), then run
   `.venv/bin/python tools/changelog.py` to update CHANGELOG.md. The tests check they match.
6. Push to your fork and open a **pull request** here. GitHub runs the tests automatically.

## Good things to know

- **Using AI is fine.** Please run the app and the tests yourself before
  opening a pull request, and say in the PR what you checked.
- **Be polite to data sources.** Keep refresh rates low, cache what you fetch, and never try to get around a site's
  rate limits, queues or blocks.
- **No logos or copyrighted media.** Team and league marks stay as text and colors.
- **Never commit personal details:** emails, domains, IP addresses, tokens, `.env` or `deploy.env`. Those stay on your
  machine.
- **Small pull requests** are easier to review than big ones. For a large idea, open an issue first to talk it over.

## License of contributions

By contributing you agree your changes are released under the project's license, the GNU AGPL v3.0 or later.
