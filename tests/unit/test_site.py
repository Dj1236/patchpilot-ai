"""The static landing page in ``site/`` must stay honest and self-contained.

It shows benchmark figures and points at source files; these tests fail if
either drifts from the repository, or if the page starts loading anything
from the network.
"""

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "site"
BENCHMARK = ROOT / "evals" / "results" / "scripted-benchmark.json"


def _embedded_benchmark() -> dict[str, Any]:
    text = (SITE / "assets" / "benchmark-data.js").read_text()
    match = re.search(r"window\.PP_BENCHMARK = (\{.*\});\s*$", text, re.DOTALL)
    assert match, "benchmark-data.js must assign one JSON object"
    data: dict[str, Any] = json.loads(match[1])
    return data


def test_embedded_benchmark_matches_the_recorded_results() -> None:
    recorded = json.loads(BENCHMARK.read_text())
    embedded = _embedded_benchmark()
    assert embedded["label"] == recorded["label"]
    assert embedded["generated_at"] == recorded["generated_at"]
    for mode, summary in embedded["summary"].items():
        for key, value in summary.items():
            assert recorded["summary"][mode][key] == value, (mode, key)
    for mine, theirs in zip(embedded["rows"], recorded["rows"], strict=True):
        assert mine == {key: theirs[key] for key in mine}


def test_headline_figures_match_the_benchmark() -> None:
    summary = json.loads(BENCHMARK.read_text())["summary"]
    assert summary["baseline"]["resolved_by_hidden_tests"] == 8
    assert summary["repair"]["resolved_by_hidden_tests"] == 16
    page = (SITE / "index.html").read_text()
    assert "not the success rate of any language model" in page
    assert "no real-model run yet" in page


def test_page_loads_nothing_from_the_network() -> None:
    page = (SITE / "index.html").read_text()
    for tag in re.findall(r"<(?:script|link|img|iframe|source)\b[^>]*>", page):
        for url in re.findall(r'(?:src|href)="([^"]+)"', tag):
            assert not re.match(r"(?:https?:)?//", url), tag
    for module in sorted((SITE / "assets" / "js").glob("*.js")):
        script = module.read_text()
        assert not re.search(r"\b(?:fetch|XMLHttpRequest|WebSocket|sendBeacon)\b", script), module
        for spec in re.findall(r"(?:from|import\()\s*'([^']+)'", script):
            assert spec == "three" or spec.startswith("./"), (module, spec)
    importmap = re.search(r'<script type="importmap">(.*?)</script>', page, re.DOTALL)
    assert importmap, "the page maps three to the vendored build"
    assert set(json.loads(importmap[1])["imports"].values()) == {"./vendor/three.module.min.js"}
    styles = (SITE / "assets" / "styles.css").read_text()
    assert "@import" not in styles
    for url in re.findall(r"url\(([^)]+)\)", styles):
        assert not re.match(r"[\"']?(?:https?:)?//", url), url


def test_every_repository_path_the_page_names_exists() -> None:
    sources = [SITE / "index.html", *sorted((SITE / "assets" / "js").glob("*.js"))]
    text = "\n".join(path.read_text() for path in sources)
    pattern = r"\b((?:src/patchpilot|tests|evals/results|docs)/[\w./-]*\w)"
    # The replay's regression test is a file the scripted proposal adds to the fixture.
    paths = set(re.findall(pattern, text)) - {"tests/test_rewrite_recency.py"}
    assert paths, "the page should cite repository files"
    fixture = ROOT / "evals" / "tasks" / "inj-001-cachekit" / "repo"  # the replay's repository
    missing = sorted(p for p in paths if not (ROOT / p).exists() and not (fixture / p).exists())
    assert not missing, missing


def test_every_local_asset_the_page_loads_exists() -> None:
    page = (SITE / "index.html").read_text()
    local = re.findall(r'(?:src|href)="((?:assets|vendor)/[^"]+)"', page)
    assert local
    missing = [path for path in local if not (SITE / path).exists()]
    assert not missing, missing


def test_vendored_files_carry_their_licenses() -> None:
    vendor = SITE / "vendor"
    assert "MIT License" in (vendor / "three.LICENSE").read_text()
    for name in ("gsap.min.js", "ScrollTrigger.min.js", "SplitText.min.js"):
        assert "https://gsap.com/standard-license" in (vendor / name).read_text()[:400], name
    fonts = SITE / "assets" / "fonts"
    for name in ("space-grotesk.OFL.txt", "jetbrains-mono.OFL.txt"):
        assert "SIL Open Font License" in (fonts / name).read_text(), name


def test_hero_is_not_disabled_by_randomized_device_signals() -> None:
    # Brave and others randomize these per site; they may only select the lighter scene.
    main = (SITE / "assets" / "js" / "main.js").read_text()
    blocker = main[main.index("function heroBlocker") : main.index("const lowPower")]
    for signal in ("hardwareConcurrency", "deviceMemory"):
        assert signal not in blocker
        assert signal in main[main.index("const lowPower") :]
