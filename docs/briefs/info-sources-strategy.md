# Brief: Prism Information Source Strategy Debate

## Background

Prism is a personal intelligence system that aggregates, clusters, and generates daily briefings from multiple information sources. It runs 24/7 on a Mac Studio (512GB unified memory).

**Owner profile:**
- Algorithm team TL at a major tech company
- Manages ~40 people with 1500+ GPUs (PPUE)
- Goals: ship high-impact open-source projects, 10x productivity, multiply influence in team management and company visibility
- Aspires to become a "super individual" in the AI era — the system should eventually self-evolve and proactively surface insights

**Current sources (in sources.yaml):**
1. X/Twitter: @karpathy, @swyx, @simonw (thread-depth)
2. arxiv: cs.LG, cs.CL, cs.AI (keyword filter)
3. GitHub trending (7-day, deep fetch)
4. follow_builders feed (community-curated X accounts)

**Pain point:** The owner feels the current setup is too academic/paper-heavy and doesn't serve their day-to-day needs as a TL well enough.

## Questions to Address

1. **Retain/Remove/Reweight**: Which current sources should stay? Which should be dropped or deprioritized?
2. **Missing Dimensions**: What critical information categories are absent? (e.g., industry dynamics, product trends, management insights, competitive intelligence, engineering culture, hiring signals, regulatory changes)
3. **Specific Recommendations**: Name concrete handles, feeds, channels, newsletters, podcasts, or data sources to add. Be specific — not "follow AI influencers" but actual names/URLs.
4. **Priority Framework**: How should sources be ranked/weighted for a TL vs an IC?
5. **TL vs IC Strategy**: How should a team leader's information diet differ fundamentally from an individual contributor's?

## Constraints

- Sources must be programmatically fetchable (RSS, API, scraping) — Prism is automated
- The system generates daily briefings, so sources should have daily-frequency signal
- Budget for paid APIs is available if ROI is justified
- Chinese tech ecosystem sources are relevant (user works at a Chinese tech company)
- Output should be actionable for someone who has ~15 min/day for information consumption
