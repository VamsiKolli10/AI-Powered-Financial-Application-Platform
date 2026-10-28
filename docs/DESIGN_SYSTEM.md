# Dashboard Design System

The Ledger AI dashboard (`web/`) is one React file (`web/src/App.tsx`) and one stylesheet
(`web/src/styles.css`). It has a clear visual language — dark green surfaces, a lime call to
action, mint accents, an eyebrow-plus-heading rhythm — but that language is mostly carried by
hardcoded values rather than tokens and shared components. This document records what exists
today, audits it, and proposes the tokens and component boundaries to converge on.

Audit date: 2026-10-01, against `web/src` at commit `4120518`. Rendered with mocked API data in
Chromium at 1440×900 and 390×844.

## Current status

**Score: 54 → 74 → 85 → 88/100.** The audit tables in section 1 record the 2026-10-01 baseline. Two
passes followed the same day: tokens + accessibility (→ 74), then components, real content,
single-scope metrics, labels, mobile navigation and section states (→ 85).

| Area | Baseline | Now | What changed |
|---|---|---|---|
| Visual consistency | 8 | 8 | No redesign; the look is preserved. |
| Token coverage | 3 | 9 | All values come from `:root` tokens. Outside the token block: 0 hex, 0 `rgba()`, 0 font-size or weight literals (only `50%` for circles). |
| Type & spacing scale | 3 | 8 | 9-step type scale with a 12px floor; 5 radii; 4px spacing scale. |
| Component structure | 4 | 8 | 10 primitives in `web/src/components/`, 8 feature components in `web/src/features/`; class naming from the naming table adopted (BEM-style blocks, `--` modifiers, `is-` state). |
| States | 5 | 8 | Every data section has loading, empty, unavailable and refreshing states; one failing service no longer blanks the page. |
| Accessibility | 6 | 9 | axe-core (WCAG 2.1 A/AA + best practice): **0 violations** on login, dashboard, empty account, partial outage and mobile. |
| Responsive | 7 | 9 | Mobile keeps navigation (sticky top bar with section tabs); Amount stays on screen at 390px; no page overflow. |
| Content accuracy | 5 | 9 | Greeting from `/auth/me` and local time; status card from real request results; Month/Week toggle drives the data; active nav follows scroll; one period for all money metrics; per-record currency; human labels for source codes. |

**Resolved since (2026-10-01, third pass):**
- **Frontend tests.** Vitest + Testing Library: 34 tests across `lib/` (formatting, period
  boundaries matched to the backend, labels), primitives (label association, loading buttons,
  `aria-pressed`), `MetricsRow` (cash flow = in − out, currency exclusion, unavailable data),
  `AssistantPanel` (refusals announced, expired session) and `Dashboard` (partial outage, status
  fallback, period refetch, currency note). Run with `npm test`; CI runs it before the build.
- **Status card from readiness.** `GET /api/v1/status` on the Gateway probes every service's
  `/ready`; the card shows "All services ready" or "N of 5 services unavailable" and falls back
  to the page's own requests only if that endpoint fails.
- **Single-currency totals.** Insights now reports in one currency (`currency` in the response,
  `other_currencies` for what was left out); the dashboard formats everything in that currency,
  marks mixed periods "USD only" and notes the excluded currencies under the breakdown,
  including when there are no rows in the reporting currency.
- **Audit follow-up.** Period totals use the summary's server-provided `date_from`/`date_to`;
  future transactions are excluded. During a Month/Week refresh, figures retain their loaded
  period's labels and a status message announces the requested period. Regression tests cover
  intermediate loading, browser clock skew and excluded-currency empty states.

**Still open:**
1. Fonts load from Google Fonts at runtime; self-hosting would remove the external request.
2. Dark theme only. Tokens make a light theme a token-swap, but none is defined.
3. The Playwright scenarios used for verification are not yet checked in as e2e tests.

---

## 1. Audit

### Summary

**Components reviewed:** 14 | **Issues found:** 27 (7 naming, 3 token, 8 accessibility, 9 content) | **Score:** 54/100

| Area | Score | Notes |
|---|---|---|
| Visual consistency | 8/10 | Coherent look; surfaces, CTA and section headers read as one system. |
| Token coverage | 3/10 | 6 custom properties, 2 never used; 35 distinct hardcoded colors. |
| Type & spacing scale | 3/10 | 18 font sizes, 9 radii, 18 distinct paddings, no scale. |
| Component structure | 4/10 | Everything inline in two components; no reusable primitives. |
| States | 5/10 | Loading/disabled/empty exist for some components; no error or hover parity. |
| Accessibility | 6/10 | Text contrast is good; labels, live regions and small text need work. |
| Responsive | 7/10 | Two breakpoints work, no page overflow; table hides amounts on mobile. |
| Content accuracy | 5/10 | Several hardcoded strings present fake state as real. |

### Token coverage

| Category | Defined | Hardcoded values found |
|---|---|---|
| Colors | 6 custom properties (`--ink`, `--panel`, `--line`, `--muted`, `--lime`, `--mint`). Only 4 are referenced; `--ink` and `--panel` are defined and never used. | 41 hex literals (35 unique) + 5 `rgba()` values. The page background `#07110d` is written three times instead of using `--ink`; the panel color is repeated inline in gradients. Seven near-identical light grays (`#aec1b7`, `#aec2b7`, `#b3c6bc`, `#b7c8bf`, `#b8c9c0`, `#a7bdb2`, `#e5eee9`) serve one role. |
| Typography | 2 families (DM Sans body, Manrope headings) | 18 distinct `font-size` values from `.62rem` to `clamp(3rem, 6vw, 5.8rem)`, 4 letter-spacing values. `font-weight: 800` is used on `.primary` and `.mark`, but only 400–700 are loaded, and `font-synthesis: none` means 800 renders as 700. |
| Spacing | none | 18 distinct `padding` values and 10 distinct `gap` values (`.2rem`, `.35rem`, `.4rem`, `.45rem`, `.6rem`, `.7rem`, `.75rem`, `.8rem`, `1rem`, `10vw`). |
| Radius | none | 9 values: `4px`, `9px`, `10px`, `12px`, `16px`, `18px`, `24px`, `99px`, `50%`. `9px` and `10px` are used interchangeably. |
| Elevation | none | One card shadow, one focus glow, one dot glow. |
| Motion | none | One `pulse` keyframe; no transitions on hover states. |

### Naming consistency

| Issue | Where | Recommendation |
|---|---|---|
| Same style, two names | `.pill` and `.source` (and `.trust-row span`) share one rule | One `.badge` class with modifiers. |
| Same meaning, two names | `.error` (login) and `.banner` (dashboard) are both "error message" | `.alert` with `--error` / `--info` modifiers. |
| State encoded as separate classes | `.status-dot`, `.alert-dot`, `.muted-dot` | `.dot` + `.dot--ok`, `.dot--alert`, `.dot--muted`. |
| Button names mix role and look | `.primary` vs `.text-button` | `.button` + `.button--primary`, `.button--ghost`. |
| Bare element selectors carry component styles | `aside`, `nav`, `table`, `th`, `td`, `input`, `textarea` | Scope to classes (`.sidebar`, `.data-table`, `.field`) so new markup doesn't inherit them. |
| Context-specific overrides | `.metric-grid .alert-metric strong`, `.assistant-panel p`, `td:first-child`, `td em` | Express as component variants (`.metric--alert`, `.panel--accent`). |
| Brand class name is ambiguous | `.mark` (logo tile) collides with HTML `<mark>` semantics | `.brand-mark`. |

### Component completeness

✅ present · ⚠️ partial · ❌ missing

| Component | Variants | States | A11y | Docs | Score |
|---|---|---|---|---|---|
| Button | ⚠️ primary + text only | ⚠️ disabled/loading on primary; no hover, no custom focus | ⚠️ native focus ring only | ✅ (this doc) | 6/10 |
| Text input / textarea | ⚠️ one style | ⚠️ focus only; no error, disabled | ❌ assistant textarea has no label | ✅ | 5/10 |
| Panel (card) | ⚠️ default + assistant accent (via selector) | — | ✅ | ✅ | 7/10 |
| Metric tile | ⚠️ default + alert (via selector) | ❌ no loading/empty | ✅ | ✅ | 6/10 |
| Section header (eyebrow + h2) | ✅ | — | ⚠️ right-slot content unstyled ("6 shown") | ✅ | 7/10 |
| Badge / pill | ⚠️ neutral + "Review" warning (`td em`) | — | ⚠️ raw API values (`llm`, `fallback`) shown to users | ✅ | 5/10 |
| Status dot | ✅ ok / alert / muted | — | ❌ color is the only read/unread signal | ✅ | 5/10 |
| Progress bar | ✅ | ❌ no empty state | ❌ no `role="meter"` or value | ✅ | 5/10 |
| Data table | ⚠️ one | ⚠️ "Categorizing…" cell; no empty/loading | ⚠️ no caption; amounts scroll off-screen on mobile | ✅ | 5/10 |
| Alert row | ✅ read / unread | ✅ empty | ⚠️ unread conveyed by color only | ✅ | 6/10 |
| Inline error / banner | ⚠️ two classes for one job | — | ❌ not announced (`role="alert"` missing) | ✅ | 4/10 |
| Brand mark | ✅ | — | ⚠️ decorative "L" read aloud; Manrope has no fallback stack | ✅ | 6/10 |
| Sidebar nav | ⚠️ | ⚠️ `active` hardcoded on Overview | ⚠️ hidden entirely below 700px | ✅ | 4/10 |
| Loading screen | ✅ | ✅ | ❌ no live region; pulse ignores `prefers-reduced-motion` | ✅ | 5/10 |

### Accessibility findings

Measured text contrast is good: every text color passes WCAG AA (lowest is the alert timestamp
`#6f887b` at 4.59:1 and table headers `#718b7e` at 4.77:1 on the panel; body text is 15.8:1; the
primary button is 12.8:1). The problems are elsewhere:

1. **Unlabeled control.** The assistant `<textarea>` has no `<label>` or `aria-label`.
2. **Nothing is announced.** Assistant answers, the loading screen and error banners update
   silently for screen readers. Add `aria-live="polite"` to the answer and `role="alert"` to errors.
3. **Color-only meaning.** Read vs. unread alerts, and positive vs. negative amounts, are
   distinguished only by color (amounts keep their sign, so they are partly safe).
4. **Tiny text.** The "Review" badge is `.62rem` (~10px), table headers `.66rem`, eyebrows
   `.69rem`. Keep functional text at 12px or above.
5. **Weak component boundaries.** Input borders are 1.78:1 against their background and panel
   borders 1.39:1 (WCAG 1.4.11 asks 3:1 for boundaries needed to identify a control). Inputs are
   still findable by their darker fill, but borders should carry it.
6. **Focus styles are inconsistent.** Inputs remove the outline and use a mint border (11.5:1,
   fine); buttons and nav links fall back to the browser ring. Define one `:focus-visible` style.
7. **Motion.** The loading pulse runs regardless of `prefers-reduced-motion`.
8. **Hidden navigation.** Below 700px the nav and system card are removed; there is no menu.

### Content and data presentation

These are not styling problems but they are part of what the system presents as truth:

| Issue | Where |
|---|---|
| "Good morning, Vamsi." is hardcoded — every user is greeted as Vamsi, at any hour. | `App.tsx:126` |
| "All systems operational · Gateway + 4 services" is static, not wired to `/ready`. | `App.tsx:122` |
| "This month" period chip is static and not interactive. | `App.tsx:126` |
| Metric scopes are mixed: "Money in" and "Cash flow" come from the latest 50 transactions, "Money out" from the month's summary, so cash flow ≠ in − out. | `App.tsx:87–132` |
| Currency is hardcoded to USD even though every record carries `currency`. | `App.tsx:12` |
| API values shown raw: `llm`, `rules`, `user`, `pending`, `fallback`, `cache`. | `App.tsx:138, 160` |
| Error messages never surface: `api.ts` reads `payload.detail`, the API returns `payload.error.message`. | `api.ts:26` |
| Anomaly threshold `0.8` is duplicated in the UI instead of coming from the API or a constant. | `App.tsx:160` |
| On mobile, the transactions table scrolls horizontally and the **Amount** column starts off-screen. | 390px render |

### Priority actions

1. **Introduce tokens and use them everywhere.** Adopt the token block in section 2, replace the 41
   hex literals and the ad-hoc sizes, and delete unused `--ink`/`--panel` duplicates. This is
   mechanical, makes a light theme possible, and removes most future drift.
2. **Fix the accessibility gaps that are one-liners:** label the textarea, add `role="alert"` and
   `aria-live`, a shared `:focus-visible` ring, a non-color unread indicator, a reduced-motion guard,
   and a 12px floor for functional text.
3. **Extract primitives from `App.tsx`:** `Button`, `Field`, `Panel` + `PanelHeader`, `Metric`,
   `Badge`, `StatusDot`, `Meter`, `DataTable`, `AlertRow`, `Banner`. Rename classes to the scheme in
   the naming table as you go.
4. **Stop presenting fake state:** greet from `/auth/me`, drive the status card from `/ready` (or
   remove it), map source codes to human labels, compute all metrics from one scope.
5. **Make the table responsive:** pin the Amount column, or switch to stacked rows below 700px.

---

## 2. Tokens

### Baseline values (before 2026-10-01)

| Token | Value | Role |
|---|---|---|
| `--ink` | `#07110d` | Page background (defined, unused) |
| `--panel` | `#0d1c16` | Panel background (defined, unused) |
| `--line` | `#20382e` | Borders and dividers |
| `--muted` | `#8ca397` | Secondary text |
| `--lime` | `#b9f27c` | Primary action, brand mark, positive amounts |
| `--mint` | `#5ee0ae` | Eyebrows, focus, healthy status, bar start |
| Fonts | DM Sans 400–700 (body), Manrope 500–700 (headings) | Loaded from Google Fonts |

### Token set (implemented)

The authoritative list is the `:root` block at the top of `web/src/styles.css`. It consolidates
values that already existed rather than redesigning, plus layout and gradient tokens. A summary:

```css
:root {
  /* Surfaces */
  --color-bg:            #07110d;
  --color-bg-sunken:     #07130e;   /* inputs, summary well */
  --color-surface:       #0d1c16;
  --color-surface-raised:#13261e;
  --color-surface-accent:#17372a;   /* assistant panel */
  --color-hover:         #14281f;

  /* Lines */
  --color-border:        #20382e;
  --color-border-strong: #4d7464;   /* new: ≥3:1 boundary on every surface */
  --color-divider:       #182c23;

  /* Text */
  --color-text:          #dfece5;
  --color-text-secondary:#aec1b7;   /* replaces 7 near-duplicates */
  --color-text-muted:    #8ca397;
  --color-text-subtle:   #718b7e;   /* table headers, timestamps */
  --color-text-on-accent:#102116;

  /* Brand & semantic */
  --color-accent:        #b9f27c;   /* lime: primary action */
  --color-highlight:     #5ee0ae;   /* mint: eyebrow, focus, ok */
  --color-positive:      #b9f27c;
  --color-negative:      #ffb09d;
  --color-warning:       #ffb28d;
  --color-warning-border:#704231;
  --color-danger-text:   #ffc5b9;
  --color-danger-bg:     #321d18;
  --color-danger-border: #744135;
  --color-alert:         #ff9d6c;
  --color-inactive:      #536b60;

  /* Typography */
  --font-body:    "DM Sans", system-ui, sans-serif;
  --font-display: "Manrope", "DM Sans", system-ui, sans-serif;
  --text-xs:   .75rem;   /* 12px floor: eyebrows, badges, table headers */
  --text-sm:   .8125rem; /* 13px: table cells, captions */
  --text-base: 1rem;
  --text-lg:   1.2rem;   /* panel titles */
  --text-xl:   1.65rem;  /* metric values */
  --text-2xl:  clamp(2rem, 4vw, 3.3rem);    /* page title */
  --text-hero: clamp(3rem, 6vw, 5.8rem);    /* login headline */
  --weight-regular: 400; --weight-medium: 600; --weight-bold: 700;
  --tracking-eyebrow: .14em; --tracking-tight: -.05em;

  /* Spacing (4px base) */
  --space-1: .25rem; --space-2: .5rem; --space-3: .75rem; --space-4: 1rem;
  --space-5: 1.25rem; --space-6: 1.5rem; --space-8: 2rem; --space-10: 2.5rem;

  /* Radius */
  --radius-sm: 4px;  --radius-md: 10px; --radius-lg: 16px;
  --radius-xl: 24px; --radius-pill: 999px;

  /* Elevation & motion */
  --shadow-card:  0 28px 80px rgba(0,0,0,.25);
  --focus-ring:   0 0 0 3px rgba(94,224,174,.45);
  --duration-fast: 120ms; --ease-standard: cubic-bezier(.2,.0,.2,1);
}
```

Mapping notes: `9px`/`10px`/`12px` radii collapse to `--radius-md`; `16px`/`18px` to
`--radius-lg`; `.62rem`–`.78rem` text collapses to `--text-xs`/`--text-sm`. The focus ring alpha is
raised from `.08` (barely visible) to `.45`.

---

## 3. Components

### Inventory

| Component | File | Props | Classes |
|---|---|---|---|
| `Button` | `components/Button.tsx` | `variant: "primary" \| "ghost"`, `loading`, `loadingLabel`, button attributes | `.button`, `.button--primary`, `.button--ghost` |
| `Badge` | `components/Badge.tsx` | `variant: "neutral" \| "warning" \| "alert"`, `description` (tooltip + screen-reader text) | `.badge`, `.badge--*` |
| `StatusDot` | `components/StatusDot.tsx` | `tone: "ok" \| "alert" \| "muted"` (always decorative) | `.dot`, `.dot--*` |
| `BrandMark` | `components/BrandMark.tsx` | — | `.brand-mark` |
| `Banner` | `components/Banner.tsx` | `variant: "block" \| "inline"`; renders `role="alert"` | `.banner`, `.banner--*` |
| `Field` | `components/Field.tsx` | `label`, `hideLabel`, `multiline`, input/textarea attributes | `.field`, `.field__label`, `.field__control` |
| `SegmentedControl` | `components/SegmentedControl.tsx` | `label`, `options`, `value`, `onChange`; buttons use `aria-pressed` | `.segmented`, `.segmented__option` |
| `Panel`, `PanelHeader`, `EmptyState` | `components/Panel.tsx` | `variant: "default" \| "accent"`, `busy`, `as`, `labelledBy`; header `eyebrow`, `title`, `aside` | `.panel`, `.panel--accent`, `.is-busy`, `.panel-header`, `.empty-state` |
| `Metric` | `components/Metric.tsx` | `label`, `value` (`null` → "—" + "Unavailable" for screen readers), `caption`, `tone` | `.metric`, `.metric--alert`, `.metric__*` |
| `Meter` | `components/Meter.tsx` | `value`, `max` (decorative; the figure is printed beside it) | `.meter` |

Feature components in `web/src/features/`: `Login`, `Dashboard` (data loading and health),
`Sidebar`, `MetricsRow`, `SpendingPanel`, `AssistantPanel`, `TransactionsTable`, `AlertsPanel`.
Shared logic lives in `web/src/lib/` (`format`, `labels`, `period`, `cx`) and
`web/src/hooks/useActiveSection.ts`. API wording for codes is centralized in `lib/labels.ts`.

The entries below were written for the baseline audit and describe intent and guidance; the
inventory above is the current API.

### Button [`Button`]

Triggers an action. Two looks exist today.

| Variant | Class today | Use when |
|---|---|---|
| Primary | `.primary` | The one main action in a view: sign in, ask the assistant. Full width. |
| Ghost / text | `.text-button` | Low-emphasis actions in chrome: sign out. |

| State | Visual | Behavior |
|---|---|---|
| Default | Lime fill, dark text, 10px radius, bold | — |
| Hover | No change today | Add a subtle brightness shift. |
| Focus | Browser default ring | Replace with `--focus-ring`. |
| Disabled / loading | 60% opacity, `cursor: wait`, label swaps to "Signing in…" / "Thinking…" | Not clickable while a request is in flight. |

**Accessibility:** native `<button>`; the loading label change is visible but not announced —
pair it with `aria-busy="true"` on the form.

```tsx
<Button variant="primary" loading={asking} loadingLabel="Thinking…">Ask Ledger AI</Button>
```

| ✅ Do | ❌ Don't |
|---|---|
| One primary button per panel | Use lime for non-actions (it already doubles as "positive amount") |
| Keep the label a verb phrase | Rely on opacity alone to show loading |

### Field [`Field`] — text input and textarea

| Property | Today |
|---|---|
| Fill / border | `#07130e` / `#294438`, 10px radius, `.85rem` padding |
| Focus | Mint border + 3px mint glow at 8% |
| Label | Login wraps the input in a `<label>` (good); assistant textarea has none |
| Error | None — errors render as a separate paragraph below the form |

Proposed API: `<Field label hint error as="input|textarea" />`, rendering the label, the control,
and an error tied with `aria-describedby`.

### Panel [`Panel`, `PanelHeader`]

The main container. Gradient surface (`rgba(19,38,30,.96)` → `rgba(10,24,18,.95)`), 1px
`--line` border, large soft shadow, 18px radius (16px for metric tiles, 24px for the login card).

| Variant | Today | Use when |
|---|---|---|
| Default | `.panel` | Content groups: breakdown, table, alerts. |
| Accent | `.assistant-panel` | The single interactive AI surface. Brighter green gradient. |

**PanelHeader** is the eyebrow + title pattern used by every panel: a mint, uppercase, tracked
`.eyebrow` above a Manrope `h2`, with an optional right slot (today: a source badge or the
unstyled "6 shown" count — give the slot the muted badge style).

### Metric [`Metric`]

A KPI tile in the 4-up grid (2-up below 1000px). Label (muted, small), value (Manrope, 1.65rem),
caption (muted, small).

| Variant | Today | Use when |
|---|---|---|
| Default | `.metric-grid article` | Neutral figures. |
| Alert | `.alert-metric` (value turns `#ffb28d`) | A count that needs attention. |

Missing states: loading (skeleton) and unavailable (e.g., summary failed).

### Badge [`Badge`]

Small rounded label. Today three selectors share it: `.pill` (category source in the table),
`.source` (summary source), `.trust-row span` (tech stack on login). A fourth, warning-styled
badge is the "Review" `<em>` inside a table cell.

| Variant | Use when |
|---|---|
| Neutral | Metadata: source, tag. |
| Warning | A row needs human review (anomaly score ≥ 0.8). |

Show human labels, not API codes: `llm` → "AI", `rules` → "Rules", `user` → "Edited",
`pending` → "Pending", `cache` → "AI (cached)", `fallback` → "Template".

### Status dot [`StatusDot`]

8px circle. `ok` (mint with glow), `alert` (orange), `muted` (gray). Used for system status and
read/unread alerts. Always pair with text or a visually hidden label ("Unread").

### Meter [`Meter`] — category bar

7px track (`#172b22`) with a mint→lime gradient fill, minimum 5% width so small values stay
visible. Add `role="meter"` with `aria-valuenow`/`aria-valuemax`, or mark it decorative since the
amount is printed beside it.

### Data table [`DataTable`]

Uppercase, tracked 0.66rem headers; 0.82rem cells; dividers in `#182c23`; first column brighter
and bold; last column right-aligned. Amounts are colored `--negative`/`--positive`.

Recommendations: add `font-variant-numeric: tabular-nums` to the amount column so digits align,
add a visually hidden `<caption>`, and below 700px either pin the amount column or stack rows.

### Alert row [`AlertRow`]

Three-column grid: status dot, title + message, timestamp. Titles come from the notification type
with underscores replaced and CSS `text-transform: capitalize` ("Anomaly Flagged"). Empty state:
"No recent alerts. That is usually good news." Prefer a lookup of display titles over CSS
capitalization so wording can be controlled ("Unusual transaction").

### Banner [`Banner`]

Error message block: `#321d18` fill, `#744135` border, `#ffc5b9` text. The login form uses a
bare `.error` paragraph for the same purpose. Merge them and add `role="alert"`.

### Brand mark, sidebar, loading

- **Brand mark:** 38px lime tile with a Manrope "L". Hide it from screen readers
  (`aria-hidden`), since "Ledger AI" follows it, and give it the full font fallback stack (it
  renders in a serif fallback when Manrope fails to load).
- **Sidebar:** 230px sticky column with brand, anchor nav, system card and sign-out. The active
  item is hardcoded; derive it from the hash or scroll position. Provide a compact menu below 700px
  instead of hiding navigation.
- **Loading:** full-screen centered pulse and text. Add `role="status"` and stop the animation
  under `prefers-reduced-motion: reduce`.

---

## 4. Patterns

| Pattern | How it is built today |
|---|---|
| Page layout | Sidebar (230px) + fluid main, max 1600px, `clamp(1rem, 4vw, 4.5rem)` side padding. Collapses to a single column with a top bar at 700px. |
| Dashboard grid | 4-up metrics → 1.45fr / 0.8fr main grid → full-width table → full-width alerts. |
| Forms | Stacked label + field, full-width primary button, inline error above the button. |
| Async feedback | Button label swap while loading; full-page loading screen on first load; 401 logs out. |
| Empty states | Alerts have one ("That is usually good news"). Breakdown, table and metrics do not. |

## 5. Breakpoints

| Width | Changes |
|---|---|
| > 1000px | Full layout. |
| ≤ 1000px | Metrics 2-up; main grid single column; login single column. |
| ≤ 700px | Sidebar becomes a sticky top bar: brand and sign-out on one row, section tabs below (horizontally scrollable). Status card hidden — failures still show in the page banner. Header stacks; Month/Week toggle moves under the greeting. Table drops Source and Date so Amount stays visible. |

## 6. Maintaining this document

Update it when you add a component, introduce a token, or change a breakpoint. Once the tokens
in section 2 are in `styles.css`, re-run the audit counts (hex literals, distinct font sizes,
radii) and update the summary score.
