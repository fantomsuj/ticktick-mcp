# Claude Context for TickTick

See AGENT.md for scheduling rules.

---

## TickTick Projects

### Inbox (Default Capture)
- **Inbox**: `699a5943b1bed115b35b1e10` — Default for quick captures; unprocessed tasks land here

### Tier 1 — Core Focus
- **Bedrock Robotics**: `69547156d4ca9147cf3c78fa` — Full-time job, highest priority
- **BSIF Fellowship**: `6988fcb958ca9155b99ecc3f` — Fellowship to build a new startup (Extensible); second highest priority
- **Tools**: `693a3b6a34db910305e570fc` — Personal projects I'm building; high priority
- **AI Research**: `6925de124d1951f8c0a709b0` — Complements building tools; high priority

### Tier 2 — Extensible (New Startup, under BSIF)
These three projects are all part of building Extensible, the startup I'm working on through the fellowship:
- **GTM & Relationships**: `6757d67c8f0808587783ab86` — Go-to-market strategy and relationship management for Extensible
- **Product & Engineering**: `6851d328bc6ad1525900e1df` — Product and engineering work for Extensible
- **Strategy & Research**: `69239e3c064f51f8c0a66b2f` — Documentation, ideas, pitch competitions, courses for Extensible

### Tier 3 — Active Side Projects & Work
- **PS Agency**: `67ae557f9ebd91593b682a01` — Web design agency I still run
- **CS Study**: `6828b39ea96b91032980817c` — Computer science studies (ongoing)
- **Startup Inbox**: `686c57f73c47910441e8f414` — Random startup ideas (overlaps with Tools; can treat as capture bucket for startup/tool ideas)

### Tier 4 — Background / Low Touch
- **Admin & Errands**: `69239f54252c91f8c0a68ad4` — Personal admin, errands, miscellaneous
- **Relationships & Social**: `69239fd13854d1f8c0a69082` — Social and relationship maintenance
- **Afore Cap**: `68ccc3155baf11eddd3914db` — Former employer (VC fund); low activity
- **BASIS VP**: `681102b0fb161104da46de81` — Future venture fund idea; storage for tasks
- **Applimize**: `680fb5bf9c3dd104da46713d` — Old company; winding down

### Other Active (Lower Priority)
- **Chaumet Office**: `66884177becd911b75279a94`
- **UNC General**: `668b7ff488305102443ea121`
- **Tech**: `6695fb18fdb29194d7492736`
- **Routines**: `6695fb3aab509194d7492975`
- **Reading**: `669c9bd88f088125da4c32bf`
- **General Personal**: `669df8928f089169e9760578`
- **Real Estate**: `66c2a448151bd14d76dab830`
- **Connecting People**: `66c2a48e45f3514d76dab9f0`
- **Private Equity**: `66c36b538f08a02ea8eedeaf`
- **Applications**: `66d74dacbb201101c5b3232b`
- **Public Markets**: `66f6b22b8e53512eb9cf07a0`
- **Active Deals & Projects**: `66f6b2938ba3112eb9cf0f11`
- **Venture Capital**: `672e789064be5181d618b716`
- **PPE**: `673a768c7a9a519a677d41c3`
- **Reframe**: `673a7a716a1a119a677d590d`
- **Crypto**: `6748ad361bda5112cf35c4be`
- **Energy**: `684db6c3227ed1033cf0fd47`

---

## Priority System

TickTick priorities map to my GTD approach:
- **High (5)** — Urgent, must do today or ASAP
- **Medium (3)** — Next actions; things I intend to do soon
- **Low (1)** — Someday/maybe or low-urgency
- **None (0)** — Inbox / unprocessed

"Next actions" in GTD = **medium priority**.
"Waiting for" tasks go in the **Work project** (closest match: Bedrock Robotics or GTM & Relationships) with the title prefix `WAITING:`.

---

## Workflows

### Weekly Review
When I say **"weekly review"**, do this sequence:
1. Show all overdue tasks
2. Show all high-priority tasks
3. Show all tasks due this week
4. Ask me what I want to reschedule, complete, or delete
5. Execute my decisions one at a time, confirming each

### Daily Planning
When I say **"plan my day"**:
1. Show overdue tasks
2. Show tasks due today
3. Help me prioritize and suggest a rough time-block order
4. Ask if I want to add, reschedule, or drop anything

---

## Behavioral Preferences

- **Always confirm before deleting tasks** — never delete without explicit yes
- **Always confirm before marking complete** — never complete without confirming
- **When creating multiple tasks, use `batch_create_tasks`** — not one-by-one
- **Default project for unspecified tasks: Inbox** (`699a5943b1bed115b35b1e10`) — treat it as the capture bucket
- **For work-related task captures: ask for due date and priority** before creating
- **"Waiting for" tasks**: prefix title with `WAITING:` and always place in **Bedrock Robotics** (`69547156d4ca9147cf3c78fa`) regardless of context — single home for all waiting tasks
- **Extensible tasks** (startup): ask which of the three Extensible projects it belongs to (GTM, Product, or Strategy) unless obvious from context
