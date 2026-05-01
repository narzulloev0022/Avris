# Light Theme — RAL 9001 Cream

Adopted in commit `e50745e` (Session 3, 2026-04-30). All colors below apply only to `body[data-theme="light"]`. Dark theme palette is unchanged.

## Surface tokens

| Role                | Hex        | CSS var(s)                                      |
|---------------------|------------|-------------------------------------------------|
| Page background     | `#E9E0D2`  | `--bg`, `--bg-base`                             |
| Cards / surfaces    | `#F2EDE5`  | `--bg-surface`, `--bg-card`, `--bg2`, `--card`  |
| Sidebar / elevated  | `#EDE6DA`  | `--bg-elevated`, `--card2`                      |
| Inner panels        | `#F8F5F0`  | (used directly: SOAP cards, waveform, transcript) |
| Diagnosis pills     | `#F0EBE3`  | (`.pat-ctx-diag` background)                    |

## Text tokens

| Role            | Hex       |
|-----------------|-----------|
| Primary         | `#1a202c` |
| Secondary       | `#4a5568` |
| Muted           | `#8a8275` (warmer than the old `#a0aec0` to keep contrast on cream) |
| Accent          | `#4AA391` |
| Mobile "Обновлено" override | `#5C5C5C` |

## Brand & status

| Role                | Hex                                            |
|---------------------|------------------------------------------------|
| Accent (`--accent`) | `#4AA391`                                       |
| Accent dark (gradient pair) | `#1A4A3E`                              |
| Critical gradient   | `linear-gradient(135deg, #8B2020, #C53030)`    |
| Stats card gradient | `linear-gradient(135deg, #1A4A3E, #4AA391)`    |
| AI badge gradient   | `linear-gradient(135deg, #1A4A3E, #4AA391)`    |
| Success / Warn / Danger backgrounds | `rgba(16,185,129,.08) / rgba(245,158,11,.08) / rgba(239,68,68,.08)` |

## Borders & shadows

| Role                | Value                                       |
|---------------------|---------------------------------------------|
| Default border      | `rgba(0,0,0,0.08)`                          |
| Hover border        | `rgba(0,0,0,0.12)`                          |
| Topbar border-bottom | `rgba(0,0,0,0.05)` (very subtle to blend)  |
| Card shadow         | `0 2px 8px rgba(0,0,0,0.06)`                |
| SOAP shadow         | `0 1px 4px rgba(0,0,0,0.06)`                |

## Card-shape conventions

| Component               | Radius | Padding |
|-------------------------|--------|---------|
| Patient card / panel    | 16px   | 16–24px |
| SOAP S/O/A/P card       | 12px   | 16–20px |
| Stats / metric tile     | 12px   | 14px 16px |
| Diagnosis pill          | 8px    | 2px 8px |
| AI / Claude badge       | 10px   | 6px 16px |
| `.btn` standard         | `var(--radius-input)` (8px) | — |
| Theme toggle (topbar)   | 50% (36×36 circle) | — |

## Iconography

- All custom icons are **inline SVG**, `stroke: currentColor`, `stroke-width: 1.5`, `fill: none`.
- Topbar icons: `20×20`.
- Settings tabs: `18×18` desktop, `16×16` mobile.
- Lucide-style geometry (user, bell, globe, sun, moon, info).

## SOAP cards (light)

```css
body[data-theme="light"] .soap-c{
  background:#FFFFFF;
  border:1px solid rgba(0,0,0,0.06);
  border-left:3px solid #4AA391;
  border-radius:12px;
  box-shadow:0 1px 4px rgba(0,0,0,0.06);
  padding:20px;
}
body[data-theme="light"] .soap-hdr h5{
  font-weight:600;
  color:#1A4A3E;
  font-size:13px;
  letter-spacing:0.5px;
  text-transform:uppercase;
}
body[data-theme="light"] .soap-ai-tag{
  background:linear-gradient(135deg,#1A4A3E,#4AA391);
  color:#fff;
  border-radius:6px;
  padding:2px 8px;
  font-size:11px;
  font-weight:600;
}
```

All four S/O/A/P cards share the same `#4AA391` left border (no more multi-color blue/green/amber/teal differentiation — it was visually noisy on cream).

## Mobile (≤480px) tweaks

- Stats bar: full-width red critical card on top, 2×2 grid of green tiles below. See `Session3-Progress.md` for full layout.
- "Обновлено" timestamp: `color:#5C5C5C; font-weight:500; font-size:12px` (light theme only — the default white-on-cream was unreadable).
- Settings tabs: horizontal scroll, icon stacked over 11px label.
