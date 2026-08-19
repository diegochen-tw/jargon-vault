---
id: demo-graph-yield
name: First-pass yield dropped one step at week 7 and never recovered
template: graph
fields:
  figure_type: Line chart, single series
  axes: 'x: production week (W1–W12), y: first-pass yield (%)'
  takeaway: The loss is a one-time step at W7, not a gradual drift
  pitfall: y-axis starts at 96%, so a 1.9-point drop looks like a cliff
  source: Line 3 weekly quality report, internal
tags:
- Concept
- Data
attachments: []
created: 1720000900.0
updated: 1720000900.0
history: []
---

![](/assets/demo-graph-yield/yield-by-week.png)

Each dot is one production week; the height is the share of units that passed every station on the first attempt, with no rework. Higher is better. The six blue weeks are the baseline; the red segment marks where the level changes.

What it shows is {{green:a step, not a slope}} — W1–W6 sit around 98.6% and W7–W12 sit around 96.6%, each stable within its own band. That pattern points at something that changed once (a new material lot, a fixture swap, a recipe edit) rather than something wearing out gradually. What the figure does **not** establish is *which* change: it has no station breakdown and no defect codes, so it can narrow down the week but not the cause.

> Read the axis before the shape. The drop is real, but it is 1.9 percentage points — the cliff is a drawing choice.
