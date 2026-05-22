---
name: renewal-reminders
description: "Surface vendor contracts with a renewal date inside a configured look-ahead window, plus any open-ended contracts that have no end date. Load when the user asks 'what's coming up for renewal?', when `wiki run renewal-reminders` invokes you, or on a scheduled monthly sweep. Writes one page to `outputs/renewals/<run_date>.md`; re-running on the same date overwrites."
license: MIT
---

# renewal-reminders

> **⚠️ Not yet shipped in v2.0.0.dev.** This operation runs via
> `wiki run renewal-reminders`, and `wiki run` is a stub in
> v2.0.0.dev: it prints `wiki run: not yet implemented (v2 migration
> in progress, see RFC-0001).` and exits non-zero (see
> `llm_wiki_kit/cli.py:_cmd_run`). The operation runner lands in
> Phase D of the v2 migration. Until then, treat this SKILL.md as the
> *design spec*, not an executable playbook. Tracked under
> retro-review concern C7 (issue #23).

Walk `wiki/vendor-contracts/`, find contracts whose
`contract_renewal_date` is inside the look-ahead window, and write
one durable page listing them. The point of this operation is one
specific failure mode: a vendor contract auto-renews because nobody
noticed the notice deadline.

## When to load

- The user asks "what's coming up for renewal?", "anything due in the
  next quarter?", etc.
- `wiki run renewal-reminders` runs you with the contract from
  `contract.yaml`.
- A scheduled monthly invocation.

## Inputs

From the operation contract:

- **`lookahead_days`** — how far ahead to look. Default 90. The
  rationale: 90 days is typically longer than the longest standard
  notice period, so a single monthly sweep won't miss anything.
- **`include_open_ended`** — whether to surface contracts with no
  `contract_end`. Default true. Open-ended contracts are the silent
  cost-accrual risk; the operation lists them in a separate section.

## Procedure

1. **Find every vendor-contract page.** Walk
   `wiki/vendor-contracts/`. Use the `wiki-search` skill with
   `--type vendor-contract`.
2. **Bucket each contract:**
   - **Overdue** — `contract_renewal_date` is in the past *and*
     `status: active`. These should have been handled; surface
     prominently.
   - **Due in the next 30 days.**
   - **Due in 31–`lookahead_days` days.**
   - **Open-ended** — no `contract_end` and (when set) no
     `contract_renewal_date`. Include only when
     `include_open_ended` is true.
   - **Out of scope** — beyond the window or already archived.
3. **For each in-scope contract, extract:**
   - Vendor name (`contract_vendor`).
   - Renewal date (`contract_renewal_date`).
   - End date (`contract_end`).
   - Amount (`contract_amount`).
   - Internal owner (`contract_owner`) — wikilinked.
   - Non-standard clauses worth highlighting
     (`contract_terms_summary` entries flagged as auto-renew or
     notice-period).
4. **Compose the reminders page** at
   `outputs/renewals/<run_date>.md` with sections:
   - **Overdue.**
   - **Due in 30 days.**
   - **Due in 31–`lookahead_days` days.**
   - **Open-ended** (if included).
   - **Total annualized spend in scope** — sum of amounts where
     parseable, with a note about unparseable entries. Surface this
     because the budget framing is often what triggers action.

## Frontmatter for the reminders page

```yaml
type: renewal-reminders
status: active
provenance: synthesized
created: <today>
modified: <today>
tags: [renewals, <run_date>]
lookahead_days: <window>
```

The `renewal-reminders` type may not yet exist in
`frontmatter.schema.yaml`'s managed `types` region — that's fine for
v0.1.

## Parsing amounts

`contract_amount` is a free-form string by design (`"$24,000 / year"`,
`"€500 / month"`, `"$2,400 one-time"`). For the total-spend summary:

- Annualize monthly amounts (×12).
- Skip one-time amounts (note their existence separately).
- When the currency varies, group by currency. Don't FX-convert.
- When you can't parse, list under "Amounts not parsed" with the raw
  string and the contract wikilink.

Be conservative: a wrong total is worse than no total.

## When the window is empty

Produce a minimal page noting "no renewals due in the next
<lookahead_days> days." Re-affirming "nothing is on fire" is a
useful artifact for a monthly sweep.

## After writing

- Append a one-line summary to the running activity log.
- If anything is in the **Overdue** bucket, escalate: explicitly tell
  the user "X overdue renewals" in your post-run output, not just on
  the page. Overdue is the actionable failure mode this operation
  exists to prevent.
- For open-ended contracts older than two years, suggest the user
  re-evaluate them — they're often the legacy line items that nobody
  remembers signing.
