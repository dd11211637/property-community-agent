# 栖邻 Frontend Release Design System

This file is the implementation source of truth for the release rewrite. The
UI UX Pro Max database was queried twice. Its marketing page patterns were a
poor fit and are rejected. The verified partial matches retained here are flat
application surfaces, restrained motion, functional typography, visible focus,
and a calm teal-led service palette.

## Product direction

- Product type: role-aware community service and operations application.
- Resident mood: calm, human, reassuring, service-first.
- Maintenance mood: direct, task-oriented, mobile-readable, progress-first.
- Admin mood: controlled, dense enough for decisions, exception-first.
- Agent mood: trustworthy co-pilot with explicit understanding, confirmation,
  execution, result, and recovery states.

## Tokens

| Token | Value | Purpose |
| --- | --- | --- |
| `--ink-strong` | `#17332f` | Primary text |
| `--ink` | `#36514c` | Body text |
| `--ink-muted` | `#687b77` | Secondary metadata |
| `--canvas` | `#f4f6f2` | Application background |
| `--surface` | `#fffefa` | Primary surface |
| `--surface-muted` | `#edf2ed` | Quiet grouping surface |
| `--line` | `#d8e1da` | Borders and separators |
| `--brand` | `#145447` | Primary action and navigation |
| `--brand-soft` | `#dcebe5` | Selected state and soft emphasis |
| `--resident-accent` | `#df7259` | Resident service highlight |
| `--maintenance-accent` | `#b56b24` | Maintenance task highlight |
| `--admin-accent` | `#2d6285` | Admin decision highlight |
| `--danger` | `#b64136` | Destructive/high-risk |
| `--warning` | `#9a5c17` | Warning/attention |
| `--success` | `#28725a` | Completed/healthy |
| `--focus` | `#196fbd` | Keyboard focus ring |

- Spacing scale: 4, 8, 12, 16, 24, 32, 40, 48, 64px.
- Corners: 8px controls, 12px compact surfaces, 16px major surfaces.
- Shadows: only overlays and elevated detail panes; content hierarchy relies on
  spacing, borders, and background contrast.
- Typography: system UI stack with Chinese-first fallbacks; no external font
  request. Body 16px/1.55, metadata 13px/1.45, page title 32–40px.
- Motion: 160ms state changes and 220ms overlay movement; never animate layout
  dimensions. Disable non-essential motion under `prefers-reduced-motion`.

## Role models

### Resident

Home starts with the next useful community-service action, then current repair,
bill, announcement, and message summaries. Agent is contextual assistance, not
the entire information architecture.

### Maintenance

Home starts with assigned work, urgency, and the next executable action. Billing
and resident-only destinations are absent. Repair detail emphasizes progress and
completion workflow.

### Admin

Home starts with exceptions requiring decisions: pending queue, failed delivery,
and high-risk events. Management health is secondary evidence, not the headline.

## Interaction contract

- Native links/buttons/inputs; no clickable `div`.
- Minimum 40px desktop controls and 44px at compact/touch breakpoints.
- Visible 2px focus ring with offset; sticky UI must not obscure focus.
- Async regions preserve their footprint with skeletons and `aria-busy`.
- Errors use human-readable recovery plus development-only technical details.
- Write actions always expose pending, confirmation, success, or error feedback.
- Status always pairs color with Chinese text and, when useful, an icon.
- Full UUIDs, raw enums, database keys, and raw backend errors are excluded from
  primary UI. A central display mapper owns human-readable status and labels.

## Rejected patterns

- Shared all-role navigation, marketing heroes, glassmorphism, purple/blue AI
  gradients, card grids as the only layout, table-first resident pages, large
  entrance animation, external font dependency, emoji icons, raw status strings,
  and display of internal identifiers.

## Delivery checklist

- 4.5:1 normal-text contrast; state does not rely on color alone.
- Keyboard operation and visible focus across navigation, dialogs, and forms.
- Reduced-motion mode; no horizontal overflow at 375, 1024, 1280, and 1440px.
- Lucide SVG family only; decorative icons use `aria-hidden`.
- Role navigation and home priorities verified with resident, maintenance, admin.
- Loading, empty, error, confirmation, pending, and success states verified.

