# Wall of Faces — Plan

> Last updated: 2026-03-21
> Status: **DRAFT — design pass pending**

---

## What We're Building

A Toolforge web tool that lets Wikimedia editors build a personal A5 profile card for a community event. The tool gathers data from Wikimedia sources, presents it as a buffet of suggestions, and lets the user compose their own card. Cards are printed at the event.

---

## Key Decisions

| # | Question | Decision |
|---|----------|----------|
| Q1 | Deployment target | Toolforge Kubernetes `webservice python3.11` |
| Q2 | Authentication | Wikimedia OAuth 2.0 (authorization code + PKCE) — multi-user |
| Q3 | Stats data source | Toolforge replica DBs for counts only; MediaWiki API for all content |
| Q4 | Avatar source | HOME_WIKI + wikis with ≥75% of HOME_WIKI edit count; user picks or enters Commons filename |
| Q5 | Userbox source | Parse wikitext via API; LLM (Mistral) as additional source |
| Q6 | LLM use | Extract userbox-equivalent suggestions from wikitext (spoken languages, interests, roles, etc.) |
| Q7 | Schema management | Alembic (versioned migrations) |
| Q8 | Barnstar detection | Match `Bestand:` image filenames against a registry (see `.claude/SCRATCH.md`) |
| Q9 | Data retention | Keep until user deletes account via self-service delete |
| Q10 | "Thank You" zone | Removed — A5 card is 100% user profile; physical pins handled offline |
| Q11 | Language | Configurable parameter; all UI strings in `strings/<lang>.yml`; default Dutch |

---

## Discovery Buffet Philosophy

Every data source produces **suggestions**. Nothing is applied to the card automatically.
The user accepts or rejects each item. The buffet has four tabs:

| Tab | Dutch | What it contains |
|-----|-------|-----------------|
| **Explanation** | Uitleg | Static card anatomy overview — no data entry |
| **Infobox** | Infobox | Profile picture, home base, home wiki, userboxes |
| **Content** | Inhoud | Lead biography text, content images (up to 3), extra sections |
| **Accomplishments** | Prestaties | Barnstars, achievement badges, "proud of" items |

If a data source yields nothing, that part of the buffet is simply empty. Everything defaults to none.

---

## User Workflow

1. **Login** via Wikimedia OAuth — we receive only the username
2. **Data gathering** runs as a background job; user sees progress and can leave and return
3. **Buffet** — user accepts or rejects suggestions in each section
4. **Card preview** — live A5 preview updates as the user makes choices
5. **Save & return** — users can edit their card at any time while the tool is online
6. **Print / export** — card is rendered as a PDF; once downloaded by the organiser, a notice is shown that changes may not affect the printed result

---

## Configuration

A block at the top of `app.py` controls all community-specific settings:

```python
# --- Community configuration ---
HOME_WIKI              = "nlwiki"
HOME_WIKI_URL          = "https://nl.wikipedia.org"
HOME_WIKI_LABEL        = "nl.wikipedia.org · Wikimedia Nederland"  # Footer text on card
EVENT_NAME             = "Wall of Faces 2026"                       # Footer right on card
EVENT_COMMONS_CATEGORY = "Wikimedia_Event_2026"
SUBMISSION_DEADLINE    = "2026-05-01"  # Shown to users; cards downloaded after this date
UI_LANGUAGE            = "nl"          # Loads strings/<lang>.yml
CARD_THEME             = "wikipedia"   # CSS theme: wikipedia | editorial | minimal

# --- Gather & finalize limits ---
GATHER_COOLDOWN_SECONDS        = 3600  # Minimum time between gather runs per user
GATHER_MAX_QUALIFYING_WIKIS    = 5     # Max wikis fed to LLM per gather (cost cap)
FINALIZE_COOLDOWN_SECONDS      = 60    # Minimum time between finalizations per user
MISTRAL_MAX_WIKITEXT_CHARS     = 50000 # Max characters of wikitext sent per wiki (GDPR data minimisation)
MAX_QUEUE_DEPTH                = 20    # Max simultaneous waiting gather jobs across all users
MAX_WORKERS                    = 4     # ThreadPoolExecutor workers (gather + snapshot combined)
ADMIN_SNAPSHOT_BATCH_SIZE      = 50    # Max SnapshotQueue rows inserted per admin snapshot request
SERVER_NAME_URL                = "https://tools.wmcloud.org/wall-of-faces"  # Used by generate_pdfs.py

# --- Storage & retention ---
SNAPSHOT_STORAGE_WARNING_GB    = 5    # Warn admin when snapshot dir exceeds this (GB)
SNAPSHOT_MAX_VERSIONS_PER_USER = 10   # Maximum snapshot versions retained per user
SNAPSHOT_INACTIVE_PRUNE_DAYS   = 365  # Days of inactivity before old versions are pruned
# --------------------------------
```

---

## Constraints

- **Deadline** — `SUBMISSION_DEADLINE` is configurable; cards are downloaded before the event
- **Concurrency** — ~12 simultaneous users expected; tool must handle more without failing
- **Rate limits** — MediaWiki API rate limit is per Toolforge IP; background job queue must respect this
- **Privacy** — data controller is the tool developer, not WMF; privacy statement required
- **Replicas** — content tables not available on Toolforge replicas; wikitext always via MediaWiki API

---

## Implementation Steps

1. **Card design** — design the A5 card layout before any frontend work starts
2. **Design pass** — produce `DESIGN.md`: file structure, data model, routes, algorithms
3. **Backend** — data layer, OAuth, background job queue, API routes
4. **Frontend** — buffet UI + live card preview
5. **PDF module** — shared `render_card(profile) → PDF` used by web UI and export script
6. **Export script** — `generate_pdfs.py`: reads from DB, produces identical output to web UI
7. **Admin UI** — overview + bulk export, protected by admin account
8. **Deploy** — Toolforge Kubernetes, Alembic migrations, OAuth registration
9. **Privacy Statement** — written after implementation; enumerates actual data collected

---

## Open Items

- [ ] Template/file structure → decide in design pass
- [ ] Background job implementation → decide in design pass (thread vs Kubernetes Job vs queue)
- [ ] Toolforge outbound access to Mistral API → verify in design pass
- [ ] Privacy Statement → write after implementation
