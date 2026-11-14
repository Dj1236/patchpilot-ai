# PatchPilot AI landing page

A static project page: HTML, CSS and native ES modules, with no build step.
Design tokens, the motion system, the storyboard and the references studied
are in [DESIGN.md](DESIGN.md).

## Preview

ES modules do not load from `file://`, so serve the folder:

```
python3 -m http.server 8000 -d site
# open http://127.0.0.1:8000/
```

## Structure

| Path | Role |
|---|---|
| `index.html` | All content, readable without JavaScript |
| `assets/styles.css` | Tokens, layout, states, reduced-motion rules |
| `assets/js/main.js` | Entry: boot line, capability checks, lazy 3D hero |
| `assets/js/hero-scene.js` | The Repair Core (Three.js), loaded only on capable devices |
| `assets/js/motion.js` | Story stage, entrances, scroll, cursor, magnetic and tilt effects |
| `assets/js/architecture.js`, `replay.js`, `sections.js` | Interactive diagram, scripted replay, engineering demos, evaluation |
| `assets/js/data.js` | Static, sanitized content: module paths, defaults, the recorded run |
| `assets/benchmark-data.js` | Copy of `evals/results/scripted-benchmark.json` |
| `vendor/`, `assets/fonts/` | Vendored three.js, GSAP and fonts with their licenses |

## Rules

- No network requests at runtime, no analytics, no API keys, no model calls.
- The 3D scene runs only without reduced motion, with WebGL, and on devices
  with at least 4 cores and 4 GB; otherwise a static illustration stays.
- `tests/unit/test_site.py` checks that the benchmark copy matches the
  results file, that the page loads nothing from the network, that every
  local asset and every repository path the page cites exists, and that the
  vendored files carry their licenses. Update `assets/benchmark-data.js`
  after regenerating the benchmark.

## Hosting

Nothing is deployed. Any static host can serve this folder as is.
