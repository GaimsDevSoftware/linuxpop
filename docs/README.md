# LinuxPop website

Static site that doubles as the project landing page, plugin store, and
developer docs. Hosted on GitHub Pages directly from this `/docs` folder.

## Files

- `index.html` — landing page (hero + features + install)
- `plugins.html` — plugin store (live search + tag filter)
- `develop.html` — wizard + Python plugin guide
- `assets/style.css` — all styling
- `assets/script.js` — store search/filter
- `assets/icons/*.svg` — copied from the app's `icons/` directory
- `.nojekyll` — tells GitHub Pages not to run Jekyll on this folder
  (so the `assets/` directory isn't filtered)

## Enable GitHub Pages

After pushing the repo:

1. GitHub → Settings → Pages
2. **Source:** Deploy from a branch
3. **Branch:** `main` / folder `/docs`
4. Save → site goes live at `https://<user>.github.io/<repo>/` in ~1 min

## Updating

Plugin store cards are hardcoded in `plugins.html`. When you add a new
bundled plugin, also add a `<article class="plugin-card">` block. The
tag pills in `<div class="filter-pills">` and the `data-tags` attribute
on each card are how filtering matches.

## Updating the icons

`assets/icons/` is a copy of the app's `icons/` folder, baked into the
site so it's self-contained. Re-copy when you change icons:

```sh
cp ../icons/*.svg assets/icons/
```
