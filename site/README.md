# ChurchTune website

The project website, published with GitHub Pages at <https://mihailshumilov.github.io/ChurchAutoTune/>.
It is plain static HTML with one stylesheet: no build step, no framework, no external fonts, scripts, CDNs,
cookies, analytics or trackers.

## Structure

```
site/
├── index.html            language picker: redirects by browser language (en / uk / es, otherwise en);
│                         without JavaScript it shows a list of links
├── 404.html              shown by GitHub Pages for any missing path (absolute /ChurchAutoTune/ asset paths)
├── robots.txt
├── sitemap.xml           every page in every language, with hreflang alternates
├── assets/
│   ├── style.css         the only stylesheet (light and dark theme via prefers-color-scheme)
│   ├── logo.svg          logo and favicon
│   └── og.png            1200×630 social preview image
├── en/  uk/  es/         one folder per language, the same pages in each:
│   ├── index.html        landing page
│   ├── roadmap/index.html   milestones M0–M7, current stage, "last updated" date
│   └── guide/index.html     instructions: today's state, equipment, developers, trying the wizard on the
│                            emulator, hardware checks,
│                            the volunteer workflow ("coming later")
```

Pages link to each other with relative paths (`../`, `../../`), so the site works under the `/ChurchAutoTune/`
subpath and in a local preview.

## The update rule

**Whenever a milestone moves or a user-visible feature lands, update the roadmap and the guide in all three
languages (en, uk, es) in the same pull request.** Concretely:

1. `*/roadmap/index.html`: the milestone badges (`badge-done`, `badge-progress`, `badge-planned`) and the
   `is-done` / `is-progress` class on the milestone card, the "In short" list, and the "last updated" date.
2. `*/index.html`: the status table and banner on the landing page if the overall state changed.
3. `*/guide/index.html`: add or change instructions. Keep the existing section ids (`today`, `equipment`,
   `developers`, `install`, `tests`, `emulator`, `full-check`, `try-wizard`, `hardware-check`, `console-dumps`,
   `volunteer-workflow`, `workflow-*`, `get-help`) stable, since other pages and outside links point to them.
   When a "coming later" step becomes real, move it into its own section and drop the "coming later" tag.
4. The `Last updated` date in every page footer and `<lastmod>` in `sitemap.xml`.
5. A new page: add it to every language, to the header and footer navigation of every page, to the
   `hreflang` alternates and to `sitemap.xml`.

All three languages always have the same sections. Write for church volunteers: short sentences, plain words,
explain any technical term. Be honest about the state of the project: no invented features, numbers or
testimonials, and no compatibility claims beyond what has been checked.

Behringer, X32, Midas and M32 are trademarks of their owners; keep the non-affiliation note in the footer and do
not use their logos.

## Preview locally

Serve the parent of a folder named `ChurchAutoTune` that points at `site/`, so paths match GitHub Pages:

```bash
mkdir -p /tmp/ct-preview && ln -sfn "$PWD/site" /tmp/ct-preview/ChurchAutoTune
python3 -m http.server 8000 --directory /tmp/ct-preview
# open http://127.0.0.1:8000/ChurchAutoTune/
```

## Deployment

`.github/workflows/pages.yml` publishes the `site/` folder on every push to `main` that touches `site/**`, and on
manual dispatch. The repository's Pages source is set to "GitHub Actions".
