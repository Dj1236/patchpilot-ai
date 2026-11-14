# PatchPilot AI landing page

A static, dependency-free project page: `index.html`, `assets/styles.css`,
`assets/app.js` and `assets/benchmark-data.js`. No build step, no external
requests, no API keys. Every demonstration uses static data from the
repository and from an offline scripted run.

Preview locally:

```
python3 -m http.server 8000 -d site
# open http://127.0.0.1:8000/
```

`assets/benchmark-data.js` is a copy of
`evals/results/scripted-benchmark.json`. After regenerating the benchmark,
update the copy; `tests/unit/test_site.py` fails until it matches, and also
checks that every repository path the page cites exists.

Deployment is not configured. Any static host can serve the `site/` folder
as is (for example GitHub Pages from a folder, once the owner approves it).
