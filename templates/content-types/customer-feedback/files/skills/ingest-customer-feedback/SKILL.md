---
name: ingest-customer-feedback
description: "Ingest a customer-feedback source (support ticket, sales call note, survey response, NPS comment, churn-call summary, in-app feedback) into a structured customer-feedback page. Load from the `ingest` skill when content-type routing identifies the source as customer-originated commentary on the product, the relationship, or a recent experience. Produces one page under `wiki/customer-feedback/`, links the customer to `wiki/customers/`, the contact (if a named individual) to `wiki/people/`, and registers the page for downstream operations (status-synthesis, onboarding-pack)."
license: MIT
---

# ingest-customer-feedback

Convert one piece of customer feedback into a clean, durable wiki
page. The user pastes a ticket body, drops a call transcript excerpt,
or hands you a survey response; your job is to produce one feedback
page and the linked-customer / linked-person stubs it needs.

## When you're loaded

The `ingest` skill routes here after it has classified the source as
customer feedback. You can also be loaded directly when the user says
"log this ticket" or "capture what the customer told us on the call."

If the input could be a customer feedback *or* a customer interview,
check before assuming. The distinction:

- **Feedback** is unsolicited (the customer chose to tell you) or
  lightly solicited (a survey, a CSM ping). One topic.
- **Interview** is a scheduled, structured conversation with a defined
  research goal. Multiple topics.

When in doubt, ask.

## Inputs you'll see

- A support-ticket body (Zendesk, Intercom, plain email).
- A sales / CS call summary or transcript excerpt.
- A survey response or NPS comment.
- A churn-call summary.
- An in-app feedback message.

For each, extract:

- **`feedback_date`** — when the feedback was *given*, not today. For
  tickets, the original submission date.
- **`feedback_customer`** — the customer org. Wikilink to
  `wiki/customers/`.
- **`feedback_contact`** — the named individual when known. Wikilink
  to `wiki/people/`. Anonymous survey responses leave this empty.
- **`feedback_channel`** — `call`, `email`, `ticket`, `survey`,
  `in-app`, `chat`. Pick the most specific.
- **`feedback_sentiment`** — `positive`, `neutral`, `negative`. When
  the source is mixed, pick the dominant tone and note the other in
  the themes. Don't invent a 1–5 score the source didn't give.
- **`feedback_themes`** — 1–4 short noun phrases that summarize what
  the feedback is *about* (e.g. `onboarding`, `pricing`, `mobile-app`,
  `support-response-time`). Themes are the join key for
  `status-synthesis`.
- **`feedback_quotes`** — direct quotes worth preserving verbatim.
  Short, in-context. Don't paraphrase here — use the body for that.
- **`feedback_follow_ups`** — concrete action items the user has
  committed to or should consider. Format:
  `@owner: do the thing by YYYY-MM-DD`. Owners are wikilinks when
  internal.

## Page shape

Render the page from `_templates/customer-feedback.md`. The filename
convention is `wiki/customer-feedback/YYYY-MM-DD-<customer>-<slug>.md`,
where `<customer>` is the kebab-case customer name and `<slug>` is a
two-to-three-word theme summary. Multiple feedback items from the
same customer on the same day get `-2`, `-3` suffixes.

## Customer linking

The `feedback_customer` field must resolve to a page under
`wiki/customers/`:

1. Search `wiki/customers/` for an existing page (tolerate legal-name
   vs. common-name variants).
2. If a match exists, use its wikilink.
3. If no match, stub a new customer page with `type: customer`,
   `status: draft`, `provenance: synthesized`, and a one-line note
   "First seen in `[[customer-feedback/<this-feedback>]]`."

## Contact linking

For `feedback_contact` (when a named individual is identified):

1. Search `wiki/people/`. Tolerate common variants.
2. Match → wikilink. No match → stub a person page.
3. Anonymous feedback leaves the field empty; do not invent a name.

## Quotes vs. paraphrase

The `feedback_quotes` field is for *verbatim* customer language —
the things you'd want to read back in a quarterly review. If the
source is a paraphrased CS note ("they're frustrated with mobile"),
don't fabricate a quote. Put the paraphrase in the body and leave
`feedback_quotes` empty.

## When the feedback is sensitive

A churn-call quote or a complaint naming a specific employee can be
sensitive. If the user asks to redact a name or detail, do so in both
the body and the frontmatter, and note "redacted" in the body. Don't
silently drop content — the user should know what was removed.

## After writing

- Append a one-line summary to the running activity log.
- If themes overlap with recent feedback from other customers, note
  the pattern in the body — `status-synthesis` will surface it on the
  next sweep but a fresh observation is more useful in the moment.
- If the feedback introduced follow-ups with owners, remind the user
  that `wiki run action-item-rollup` will surface them.
