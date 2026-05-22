# customer-feedback/

One page per piece of customer feedback worth a durable note —
support tickets that exposed a recurring issue, sales-call concerns,
survey responses, NPS comments, churn-call summaries. Pages are
created by the `ingest-customer-feedback` skill (see
`skills/ingest-customer-feedback/SKILL.md`) from a ticket body, call
note, or survey response.

## Conventions

- **Filename:** `YYYY-MM-DD-<customer>-<slug>.md` where `<customer>` is
  the kebab-case customer name and `<slug>` is a two-to-three-word
  theme summary.
- **Template:** `_templates/customer-feedback.md` is the seed.
- **Linking:** `feedback_customer` is a wikilink to `wiki/customers/`;
  `feedback_contact` is a wikilink to `wiki/people/` when a named
  individual is identified (empty for anonymous surveys).
- **Frontmatter:** `type: customer-feedback` plus the feedback-scoped
  fields declared in `frontmatter.schema.yaml`'s managed `fields`
  region (`feedback_date`, `feedback_customer`, `feedback_contact`,
  `feedback_channel`, `feedback_sentiment`, `feedback_themes`,
  `feedback_quotes`, `feedback_follow_ups`).

## When to capture, when to skip

Not every customer comment earns a page. Capture when the feedback:

- Names a recurring theme.
- Names a specific risk or churn signal.
- Includes a quote you'll want to surface in a review.
- Has an attached follow-up that needs an owner.

Skip when the feedback is a one-off bug report already tracked in the
issue tracker — link to the issue from the customer page instead.

## What downstream operations read

- `status-synthesis` walks recent feedback and surfaces theme
  clusters across customers.
- `onboarding-pack` (when scoped to a specific customer) reads
  feedback for that customer to brief a new account-team member.
- `action-item-rollup` reads `feedback_follow_ups`.
