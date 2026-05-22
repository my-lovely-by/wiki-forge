---
name: onboarding-pack
description: "Produce a one-page onboarding briefing for a newcomer joining a customer or project. Load when the user asks 'brief me / brief X on the apollo project' or 'put together an onboarding pack for the acme account', when `wiki run onboarding-pack` invokes you with a `scope:` argument, or when a new team member joins. Writes one page to `outputs/onboarding/<scope>.md`; re-running on the same scope overwrites — the pack is meant to stay current."
license: MIT
---

# onboarding-pack

> **⚠️ Not yet shipped in v2.0.0.dev.** This operation runs via
> `wiki run onboarding-pack`, and `wiki run` is a stub in v2.0.0.dev:
> it prints `wiki run: not yet implemented (v2 migration in progress,
> see RFC-0001).` and exits non-zero (see
> `llm_wiki_kit/cli.py:_cmd_run`). The operation runner lands in
> Phase D of the v2 migration. Until then, treat this SKILL.md as the
> *design spec*, not an executable playbook. Tracked under
> retro-review concern C7 (issue #23).

Assemble the durable context a newcomer needs in their first week on
a customer or project — the people, the recent decisions, the
themes from customer feedback, the open risks. The output is one
page that cites every source it summarizes, so the newcomer can
follow links back into the vault rather than re-reading everything.

## When to load

- The user asks "brief me on the apollo project", "put together an
  onboarding pack for the acme account", "what should the new CSM
  read first?"
- `wiki run onboarding-pack scope=project:apollo-revamp` runs you
  with the contract from `contract.yaml`.
- A new team member joins and the user wants to hand them a single
  starting point.

## Inputs

From the operation contract:

- **`scope`** — `customer:<name>` or `project:<name>`. Required.
  `<name>` is the kebab-case page name under
  `wiki/customers/` or `wiki/projects/`.
- **`lookback_days`** — how far back to include decisions and
  feedback. Default 180. Older items are still wikilinked from the
  scope page but not summarized in the pack.

## Procedure

1. **Resolve the scope page.** Verify
   `wiki/<kind>/<name>.md` exists. If it doesn't, refuse: a pack for
   a non-existent customer / project is a typo or a missing page.
2. **Read the scope page.** Extract the DRI / account team, the
   one-line description, the active engagements, and any open risks
   the page records.
3. **Walk decisions.** For
   `wiki/decisions/*.md` whose body links to the scope page or whose
   `tags` include the scope name. Filter to those with
   `decision_date` inside the look-back window. Include the
   `decision_status` so the newcomer knows what's still proposed.
4. **Walk customer-feedback.** For a customer scope, filter feedback
   pages where `feedback_customer` matches the scope. For a project
   scope, walk feedback pages whose body links the project (less
   reliable signal — note the caveat on the pack page).
5. **Walk recent stakeholder-updates (if present).** Include the
   most-recent 3 updates for the scope as a "what we told leadership"
   summary. Stakeholder-update isn't a hard requirement of this
   operation, so handle its absence gracefully — note "no
   stakeholder-update pages found for this scope" and move on.
6. **Compose the pack page** at `outputs/onboarding/<scope>.md` with
   sections:
   - **Scope** — wikilink to the scope page; one-line description.
   - **People** — the account team / project DRI, wikilinked, with
     a one-line role per person pulled from their page.
   - **What we've decided** — recent decisions, ordered by date,
     each with a one-line summary and a wikilink. Flag decisions
     with `decision_status: proposed` separately.
   - **What customers are telling us** — themes aggregated across
     recent customer-feedback, each with the source wikilinks.
   - **What we've said to leadership** — the last 3
     stakeholder-updates (when available), most recent first.
   - **Open questions** — anything you noticed during the walk that
     the scope page doesn't yet answer. Phrase as questions, not
     assertions.
7. **Idempotence.** Re-running on the same scope overwrites. The
   pack is meant to *stay current* — it's not a historical
   snapshot. If the user wants a frozen snapshot, they should copy
   the rendered page to a dated location themselves.

## Frontmatter for the pack page

```yaml
type: onboarding-pack
status: active
provenance: synthesized
created: <today>
modified: <today>
tags: [onboarding, <scope_kind>:<scope_name>]
pack_scope: <scope_kind>:<scope_name>
pack_lookback_days: <lookback>
```

The `onboarding-pack` type may not yet exist in
`frontmatter.schema.yaml`'s managed `types` region — that's fine for
v0.1.

## What not to include

- **Internal-only quotes** that aren't safe to share with a new joiner
  who hasn't yet been read into NDA-covered material. If feedback
  pages have redacted quotes, surface the theme but not the verbatim.
- **Decisions still under debate.** A decision page with
  `decision_status: proposed` is included but flagged — the newcomer
  should know what's still up for discussion.
- **Stale archived content.** Skip pages with `status: archived`.

## When the scope is thin

A project page with one decision and no feedback is a perfectly valid
input — the pack will be short, but that's honest. Do not pad with
fabricated context. A one-page pack that accurately says "this
project is two months old; here are the two decisions and one open
risk we have" is more useful than a fake five-page pack.

## After writing

- Append a one-line summary to the running activity log.
- If the scope page has no DRI, flag it in your post-run output — a
  scope without a DRI is the kind of thing onboarding the newcomer
  *into* would expose, so naming it now is useful.
- Suggest the user share the pack URL with the newcomer. Re-running
  in a month surfaces what's changed.
