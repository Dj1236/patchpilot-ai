# PatchPilot AI landing page: design system

The page explains one idea: **a system that investigates code, finds a
failure, repairs it, and proves the repair, with every boundary enforced
outside the model.** Every visual and every animation must serve one of
those verbs. Motion that only decorates is cut.

## 1. Art direction

| Trait | How it shows |
|---|---|
| Precise | Hairline rules, a 56 px technical grid, mono labels with exact values from code |
| Security-conscious | Three colour-coded trust zones that never merge; red is used only for rejection, green only for evidence |
| Calm, then energetic | Quiet editorial sections alternate with one pinned story and one 3D hero; never two loud sections in a row |
| Honest | Every figure carries its source; "scripted" and "pending" labels are part of the visual language, not footnotes |

Avoided on purpose (from Taste Skill and Impeccable's anti-pattern lists):
purple gradients, gradient text, glass cards everywhere, emoji icons, cards
inside cards, AI-brain imagery, italic serif display, generic CTAs.

## 2. Tokens

### Colour

| Token | Value | Use |
|---|---|---|
| `--bg` | `#07090b` | page |
| `--bg-2` | `#0b0e12` | raised sections |
| `--panel` / `--panel-2` | `#10141a` / `#151a21` | panels, hover |
| `--line` / `--line-2` | `#1c232c` / `#29323d` | rules, borders |
| `--ink` / `--ink-2` / `--ink-3` | `#eef1f4` / `#a7b0bb` / `#76808c` | text levels; `--ink-3` only for labels of 12 px and up |
| `--signal` | `#5ee6d0` | the one accent: active routes, focus, the repair core |
| `--ok` | `#5ad19a` | evidence that held |
| `--bad` | `#ff6b62` | rejection |
| `--warn` | `#f2b552` | pending, untrusted, scripted |
| `--zone-host` / `--zone-sandbox` / `--zone-eval` | `#7cc4ff` / `#f2b552` / `#b49dff` | trust zones only |

### Type

- Display: **Space Grotesk** (variable, self-hosted, OFL). Headlines 600 to 650, tracking −0.035 em.
- Mono: **JetBrains Mono** (variable, self-hosted, OFL). Labels, code, numbers.
- Body: system UI stack (SF Pro, Segoe UI, Roboto); no download for reading text.
- Scale: 12 · 13.5 · 15 · 17 · 22 · clamp(30 to 48) · clamp(46 to 96) px.

### Space and shape

4 px base; section padding 128 px desktop, 76 px phone; 16 px phone
gutter. Radii 6 · 10 · 14 px. Depth comes from one light source per
section (a soft radial), never from stacked shadows.

## 3. Motion system

Engines are split so no two systems animate the same property:

| Layer | Engine | Properties |
|---|---|---|
| Timelines, scroll, text, counters | GSAP 3.13 with ScrollTrigger and SplitText | transform, opacity, clip-path, SVG stroke offsets |
| Hover, focus, state | CSS transitions | background, border, colour, CSS variables |
| Pointer-follow (cursor, magnetic, tilt, spotlight) | GSAP `quickTo`, CSS variables | transform only |
| 3D hero | Three.js render loop | WebGL only |

| Token | Value | Use |
|---|---|---|
| `micro` | 150 ms | hover colour, press |
| `ui` | 240 ms | tabs, toggles, tooltips |
| `enter` | 0.8 s, `expo.out` | section reveals |
| `cinematic` | 1.2 s, `expo.out` | hero headline, scene changes |
| `exit` | 0.35 s, `power2.in` | elements leaving; exits are faster than entrances |
| stagger | 0.06 s lines, 0.018 s characters, 0.08 s items | |
| CSS ease | `cubic-bezier(.16, 1, .3, 1)` out, `cubic-bezier(.65, 0, .35, 1)` in-out | |
| press | scale .97, 120 ms | buttons |
| lift | −2 px, 150 ms | cards |
| parallax | at most 40 px per layer, at most 6° tilt | |

Rules:

1. Entrances play once per element; scrolling back does not replay them.
2. Scrubbed animation only where scroll position means progress: hero exit, story rail, reading bar.
3. One sticky sequence on the page: the story. No horizontal scroll hijack and no scroll-smoothing library; native scroll keeps touch, keyboard and assistive technology correct.
4. Character scramble only on short mono labels and the redaction demo, never on reading text.
5. Loops (3D scene, flow pulses) pause when off-screen or when the tab is hidden.
6. **Reduced motion:** no WebGL, no scrubbing, no cursor effects, no scramble; content appears in its final state.
7. Without JavaScript every section is readable in order; animation only adds.

## 4. Storyboard

| # | Section | Motion | Why |
|---|---|---|---|
| 0 | Boot line inside the hero, non-blocking | A mono line ticks the real start-up steps: fonts, motion engine, repair core | Honest initialisation; never covers the headline |
| 1 | Hero: the Repair Core | Three.js. Repository modules orbit a wireframe core; a scan marks one module red; a packet goes to the sandbox and is rejected; a second packet passes and reaches the evaluator; the module turns green. The pointer lifts nearby modules; scrolling pulls the camera back | The product loop in one image |
| 2 | Story | Seven scenes on one sticky stage (issue, investigation, proposal, rejection, repair, verification, approval) with a rail that fills as you scroll | The workflow, told in order |
| 3 | Architecture | Modes re-frame the diagram, pulses travel the active route, zones light up, the stage tilts slightly with the pointer | Real boundaries, explorable |
| 4 | Replay | Diff lines stagger in, failing rows flash, the next patch replaces the feedback, a completion stamp draws itself | The evidence, step by step |
| 5 | Engineering | Five compositions: containment, bounds, memory, separation, consent | Each control explained by doing |
| 6 | Evaluation | Counters, bars, a scenario selector; "resolved" defined | Scripted numbers, clearly labelled |
| 7 | Status and docs | Quiet; status lights pulse slowly | Credibility |

## 5. References studied

| Reference | Taken |
|---|---|
| Taste Skill | Review at 1440, 768 and 390 px and fix the top three issues per round; one accent carries the page; a real product preview instead of filler cards |
| Impeccable | Hover lift 1 px at 150 ms, press scale about .96, list rows 70 ms apart; P0 to P3 critique; its anti-pattern list |
| GSAP skills | Timelines instead of chained delays; ScrollTrigger on timelines; refresh after layout changes; transforms over layout properties |
| Three.js skills and examples | Few draw calls (instancing), capped pixel ratio, dispose of geometry and materials, handle context loss |
| design-dna | Token categories: colour, type, spacing, shape, elevation, motion, plus style and effects |
| Vanta, ThreeUI, React Bits, Skiper UI | Ideas only: point fields, magnetic buttons, spotlight borders, direction-aware fills. No code copied |
| Lenis | Considered and not used: native scroll is more robust for touch and assistive technology |

No paid template code or assets were used.

## 6. Runtime stack

Everything is served from this folder; the page makes no network requests.

| File | Version | License |
|---|---|---|
| `vendor/three.module.min.js` | three 0.170.0 | MIT (`vendor/three.LICENSE`) |
| `vendor/gsap.min.js`, `ScrollTrigger.min.js`, `SplitText.min.js` | GSAP 3.13.0 | GSAP Standard "no charge" license, https://gsap.com/standard-license |
| `assets/fonts/space-grotesk-latin-wght-normal.woff2` | Fontsource 5.3.0 | SIL OFL 1.1 (`assets/fonts/space-grotesk.OFL.txt`) |
| `assets/fonts/jetbrains-mono-latin-wght-normal.woff2` | Fontsource 5.3.0 | SIL OFL 1.1 (`assets/fonts/jetbrains-mono.OFL.txt`) |

No bundler: native ES modules and an import map load Three.js, and the 3D
module is imported only after first paint, and only on devices that can run
it. A bundler would add a build step and a dependency tree for no runtime
gain on a single page.
