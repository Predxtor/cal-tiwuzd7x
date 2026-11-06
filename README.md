# Uni calendar feed and desktop glance

- `cal.ics`: calendar feed Apple Calendar subscribes to (deadlines, bills, weekday study blocks). Rebuilt every morning at 6:44 by `sync.py` from Sussex Canvas.
- `titles.json`: short names for assignments, matching the hub. New assignments fall back to their Canvas title.
- `hub/`: read-only desktop glance page (GitHub Pages), built from `cal.ics`. Tapping it opens the private hub.

This repo is public, so it holds no amounts, notes or personal details. Never commit `canvas.json` or the Canvas token.
