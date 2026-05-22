# stakeholder-updates/

One page per outbound status update — Friday recaps, exec emails,
weekly leadership notes, customer-facing project bulletins. Pages are
created by the `ingest-stakeholder-update` skill (see
`skills/ingest-stakeholder-update/SKILL.md`) from a paste, email body,
or slide bullets.

## Conventions

- **Filename:** `YYYY-MM-DD-<project>-<slug>.md`, where `<project>` is
  the kebab-case project name and `<slug>` is a two-to-three-word
  descriptor. Multiple updates for the same project on the same day get
  a `-2`, `-3` suffix.
- **Template:** `_templates/stakeholder-update.md` is the seed. Open it
  via the Templater command or invoke `ingest-stakeholder-update`.
- **Linking:** `update_project` is a wikilink to `wiki/projects/`;
  audience members are wikilinks to `wiki/people/` when individuals,
  plain strings when groups ("leadership", "@channel").
- **Frontmatter:** `type: stakeholder-update` plus the update-scoped
  fields declared in `frontmatter.schema.yaml`'s managed `fields`
  region (`update_date`, `update_project`, `update_audience`,
  `update_status`, `update_highlights`, `update_risks`, `update_asks`).

## What downstream operations read

- `status-synthesis` walks updates within a window and produces a
  cross-project digest, flagging colour changes and unresolved risks.
- `action-item-rollup` reads `update_asks` and pairs them with
  meeting follow-ups for an owner-grouped view.
