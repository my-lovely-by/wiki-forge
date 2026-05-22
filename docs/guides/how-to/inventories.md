# Inventories — Tracking Typed Entity Collections

> **v1-vintage page.** This how-to describes the v1 inventories pattern; some references (page-types tables in `CLAUDE.variant.md`, specific skill names like `trip-planner` or `ingest-website`) describe artifacts the v2 kit no longer ships. The underlying pattern — per-item markdown + a `.base` view — still applies; the wiring into v2's recipe/primitive surface is a future rewrite. Treat this as background reading until then.

The kit ships with a handful of curated inventories (restaurants, subscriptions, cloud tooling, SaaS contracts, advisors, role-tooling). Adding your own follows the same pattern: small per-item markdown files + a `.base` file rendering the collection.

## When to use an inventory vs. ad-hoc notes

Use an inventory when you have:

- **5+ items of the same kind** that you'll add to over time
- **Consistent attributes per item** that you want to filter or sort by
- **A use case for browsing the collection** (find all X, see all Y, sort by Z)

Use ad-hoc notes when:
- You have <5 items
- The "kind" isn't well-defined yet (hard to commit to a schema)
- You don't browse the collection — you reach for items individually

## The pattern

Every inventory has:

1. **A page-type** declared in the variant's `CLAUDE.variant.md` page-types table
2. **A template** at `_templates/{type}.md` with the inventory's frontmatter schema
3. **A folder** at the natural location (e.g., `wiki/food/restaurants/`, `wiki/tools/agentic-stack/`)
4. **A `.base` file** rendering the collection with grouped + sorted views

Optional: an `index.md` explaining the inventory; a custom content-type ingester if items routinely come from a known URL pattern.

## Adding a new inventory — step by step

### 1. Decide the schema

What attributes per item? What's the unique identifier (slug)? What status values?

Trade-off: more attributes = more capture friction, but more queryability later. Default to fewer fields; add as you discover you want them.

### 2. Pick the location

Where does it live? Default: in the natural domain folder.

- Items related to food → `wiki/food/{inventory}/`
- Items related to home → `wiki/home/{inventory}/`
- Tools → `wiki/tools/{inventory}/`
- Network → `wiki/network/{inventory}/`
- Cross-cutting items → `wiki/inventories/{inventory}/` (less preferred; only when no domain clearly fits)

### 3. Define the page-type

Add a row to your variant's `CLAUDE.variant.md` page-types table:

```markdown
| `{type}` | {Description} | `wiki/{path}/` |
```

### 4. Create the template + .base file

Author `_templates/{type}.md` with frontmatter declaring the schema (`title`, `type`, `status`, `created`, `modified`, `tags`, plus your custom fields), `## Synopsis`, and any structured body sections. Then create a `.base` file in the inventory folder filtering by `type: {type}` with views for the access patterns you care about (group by category, sort by date, etc.). See any shipped inventory's `.base` file for the YAML shape.

### 5. Populate

Start adding items. The `.base` file auto-updates as new items match the filter.

## Examples shipped in the kit

- **Family — restaurants** (`wiki/food/restaurants/`) — by cuisine; tracks last visited, family rating, kid-friendliness
- **Family — subscriptions** (`wiki/finances/subscriptions/`) — by category; tracks billing cycle, next-billing date, value-assessment timer
- **Family / Personal — holdings** (`wiki/finances/holdings/`) — by broker / asset class / account type; tracks ticker, cost basis, acquisition date, sector
- **Family / Personal — tax records** (`wiki/finances/tax/{year}/`) — per-year folder; one file per form (W-2, 1099-*, 1098, K-1); cross-references holdings; SSN-redacted
- **Family — POI catalog** (`wiki/travel/places/{location}/`) — by location / kind; tracks duration, interests, visit history; populated by [[trip-planner]]
- **Work — cloud software / agentic stack** (`wiki/tools/agentic-stack/`) — by cloud provider; tracks role in stack, install status, maintainer
- **Work — SaaS / vendor registry** (`wiki/tools/vendors/`) — by category; tracks renewal dates, contract terms, account ownership
- **Personal — advisors / mentors** (`wiki/network/advisors/`) — by relationship; tracks last contact, expertise areas, what-I-owe
- **Personal — tooling by role** (`wiki/career/tooling/`) — by role; tracks daily-driver vs occasional vs evaluating

Each ships with: a template, a folder with `index.md` + `.base` file, and a page-type entry in the variant CLAUDE.

## When to add a custom ingester

If items routinely come from a known URL pattern, a custom content-type ingester can speed capture:

- Restaurant URL (Yelp, Google Maps) → restaurant page
- Vendor website → SaaS-contract page (skeleton; manual fill of contract terms)
- Mentor's LinkedIn → advisor page (skeleton)

The ingester pattern matches the kit's other content-type ingesters (see `ingest-recipe` or `ingest-meeting` for examples). Skip ingesters for inventories where capture friction isn't the bottleneck.

## Composing inventories with operations

Once an inventory has critical mass, operations can read it:

- `recipe-recommender` reads the recipe library (an existing kit inventory)
- `networking-digest` reads people / advisors lists for follow-up surfacing
- `reading-queue` reads books for prioritization
- `follow-up-tracker` reads subscriptions for renewal-date alerts

You can write your own operation skill that reads any inventory the kit declares (or one you've added). The schema-tag (`type: {type}`) is the contract.

## Cross-cutting dashboards

A future "vault dashboard" could compose multiple `.base` views — bookmarks at the top, restaurants nearby, subscriptions due-soon, advisors stale. The kit doesn't ship this yet; the building blocks (per-inventory `.base` files + the existing `bookmark-homepage` skill as a prototype for multi-Base composition) are the foundation.
