---
inclusion: fileMatch
fileMatchPattern: "frontend/**"
---

# Material Design 3 (Material You) — Web Implementation Guide

This project applies MD3 principles via vanilla CSS/JS (no @material/web components — those are maintenance-mode). Comprehensive reference docs live at `.agents/skills/material-3/references/` — read those for deep dives.

## Reference Index

| Topic | Reference File |
|-------|---------------|
| Color roles, tonal palettes, dynamic color | #[[file:.agents/skills/material-3/references/color-system.md]] |
| Typography scale, shape tokens, elevation, motion | #[[file:.agents/skills/material-3/references/typography-and-shape.md]] |
| Component catalog (30+ components) | #[[file:.agents/skills/material-3/references/component-catalog.md]] |
| Navigation patterns (bar, rail, drawer, tabs) | #[[file:.agents/skills/material-3/references/navigation-patterns.md]] |
| Layout, breakpoints, canonical layouts, foldables | #[[file:.agents/skills/material-3/references/layout-and-responsive.md]] |
| Theming, dynamic color, dark mode, contrast | #[[file:.agents/skills/material-3/references/theming-and-dynamic-color.md]] |
| Full skill overview & decision tree | #[[file:.agents/skills/material-3/SKILL.md]] |

## Quick Token Reference (Web CSS)

### Color Tokens (`--md-sys-color-*`)

Core roles: `primary`, `on-primary`, `primary-container`, `on-primary-container`, `secondary`, `secondary-container`, `tertiary`, `tertiary-container`, `error`, `surface`, `on-surface`, `on-surface-variant`, `surface-container` (5 levels: lowest → highest), `outline`, `outline-variant`, `inverse-surface`.

Rules:
- Only pair colors in their intended pairs (e.g. `primary` + `on-primary`)
- Use `outline-variant` for dividers, `outline` for important boundaries
- Elevation = tonal surface color, not shadows (shadows only when floating over busy backgrounds)

### Typography Tokens (`--md-sys-typescale-*`)

5 categories × 3 sizes = 15 styles: Display (L/M/S), Headline (L/M/S), Title (L/M/S), Body (L/M/S), Label (L/M/S).

Each has: `-font`, `-weight`, `-size`, `-line-height`, `-tracking`

Key mappings: Buttons → Label Large, Cards → Title Medium + Body Medium, App bar → Title Large.

### Shape Tokens (`--md-sys-shape-corner-*`)

`none` (0) → `extra-small` (4px) → `small` (8px) → `medium` (12px) → `large` (16px) → `extra-large` (28px) → `full` (9999px)

Key mappings: Buttons → `full`, Cards → `medium`, Dialogs → `extra-large`, FAB → `large`, Chips → `small`.

### Motion Tokens

Easing:
- `--md-sys-motion-easing-emphasized`: `cubic-bezier(0.2, 0, 0, 1)` — elements staying on screen
- `--md-sys-motion-easing-emphasized-decelerate`: `cubic-bezier(0.05, 0.7, 0.1, 1)` — entering
- `--md-sys-motion-easing-emphasized-accelerate`: `cubic-bezier(0.3, 0, 0.8, 0.15)` — exiting

Duration: Short (50–200ms), Medium (250–400ms), Long (450–600ms), Extra-long (700–1000ms)

### Elevation via Surface Tint

| Level | Use | Token |
|-------|-----|-------|
| 0 | Flat | `surface` |
| 1 | Cards, sheets | `surface-container-low` |
| 2 | Menus, nav bar | `surface-container` |
| 3 | FAB, dialogs | `surface-container-high` |
| 4–5 | Hover/focus only | `surface-container-highest` |

## Mapping to Deepgram Design System

This project uses Deepgram's `dg-*` classes. Apply M3 principles within:

| Deepgram Class | M3 Equivalent |
|----------------|---------------|
| `dg-btn--primary` | Filled button (primary, full radius) |
| `dg-btn--secondary` | Tonal button (secondary-container) |
| `dg-btn--ghost` | Text button |
| `dg-card` | Filled/outlined card (medium radius) |
| `dg-input` | Filled text field (small top-corner radius) |
| `dg-form-label` | Label Large type role |

## Anti-Patterns

- Never hardcode colors — use `var(--md-sys-color-*)` tokens
- Never pair colors outside intended pairs (breaks contrast in dynamic/dark/high-contrast)
- Never use shadows as primary depth cue — use tonal surface color
- Never use `border-radius` literals — use shape tokens
- Never stretch content to fill ultra-wide screens — constrain to ~1040px max
- Never use `outline` for dividers (use `outline-variant`)

## Platform Note

@material/web is maintenance-mode. M3 Expressive features (spring motion, shape morphing) are NOT available on web. For this project, use CSS custom properties + standard HTML elements styled with MD3 token values.
