# Off Duty Locks — Brand Law (MASTER)

Authoritative visual law for every Off Duty Locks product surface. Any UI work in
this repo — dashboard, web app, marketing — obeys this file. Generic
palette/font generator output never overrides these tokens.

Direction: dark sports-terminal. Dense data grids, signal-color semantics, one
hot accent. Dark-first; no light mode is specified yet (do not invent one).

## Surfaces

| Token | Value | Use |
|---|---|---|
| `--odl-bg` | `#0B0B0D` | Page background (near-black graphite) |
| `--odl-panel` | `#141417` | Cards, panels, table containers |
| `--odl-border` | `#26262B` | Hairline borders, dividers, table rules |
| `--odl-text` | `#E7E7EA` | Primary text |
| `--odl-text-muted` | `#9CA3AF` | Secondary text, column headers |

## Accent — ONE accent only

| Token | Value | Use |
|---|---|---|
| `--odl-accent` | `#FF5C1C` | Active nav, primary buttons, selected tabs, line-move emphasis, brand marks |

No gradients. No second accent. No purple, gold, or neon green as UI chrome.

## Signal semantics (data meaning, not decoration)

| Token | Value | Meaning |
|---|---|---|
| `--odl-signal-sharp` | `#22C55E` | Sharp money / positive edge / green rating ring |
| `--odl-signal-model` | `#3B82F6` | Model edge |
| `--odl-signal-public` | `#EAB308` | Public heavy / caution / mid rating ring |
| `--odl-signal-rlm` | `#FF5C1C` | Reverse line move (shares the accent) |
| `--odl-signal-warn` | `#EF4444` | Warning / conflict / negative |
| `--odl-signal-none` | `#6B7280` | No clear signal / neutral |

Rating rings: green ≥ 7.5 · yellow 5.0–7.4 · orange < 5.0.

Green/red in data cells always mean favorable/unfavorable values — never use
them decoratively, and never encode meaning by color alone (pair with text).

## Typography

| Role | Face | Rules |
|---|---|---|
| Display / headers / nav | **Barlow Condensed 700** | Uppercase, tracked (+2–4%), tight leading |
| Body + all data grids | **Inter** | `font-variant-numeric: tabular-nums` on every numeric cell; 13–14px grid text |

## Motion

Minimal and fast: ~140 ms ease-out for state changes. No decorative animation,
no parallax, no scroll-triggered effects on data surfaces.

## Standing copy law

Every product surface footer carries:
"All data provided for informational purposes only. Please wager responsibly."
Marketing surfaces additionally carry "21+" and "1-800-GAMBLER".

## Terminal theme

`.pi/themes/odl.json` mirrors this identity (accent `#FF5C1C`, surfaces
`#0B0B0D`/`#141417`, signal green/red/yellow). Keep them in sync when tokens
change.
