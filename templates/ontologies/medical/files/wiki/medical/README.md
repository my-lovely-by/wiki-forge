# medical/

The household's health record: chronological visit history per person,
the current medications list, providers, insurance, and the periodic
summaries produced for visits and forms.

## Conventions

- **One medical page per person.** Filename is `{name}-medical.md` and
  matches the person page in `wiki/people/`. Entries are reverse-
  chronological — most recent visit at the top.
- **Shared registers.** `medications.md`, `providers.md`, and
  `insurance.md` cover the whole household. Keep them current; ingesters
  surface diffs but do not silently overwrite.
- **Follow-ups as callouts.** Every recheck, refill, or next-visit
  reminder is a `> [!important] Follow-up due by YYYY-MM-DD` callout on
  the relevant medical page. The `follow-up-tracker` operation scans
  these.

## Sensitivity

Medical data is the most sensitive content in the vault. Keep this
folder inside the gitignored side of the vault if you sync to a hosted
git remote, or use a separate encrypted vault. SSNs and account numbers
should never reach a wiki page — the `medical-record` ingester redacts
them on the way in.

## Created by other primitives

- `medical-record` ingester appends visit summaries, EOBs, and lab
  results; updates `medications.md` and `providers.md` as side-effects.
- `medical-summary` operation reads a person's medical page and produces
  a versioned summary suitable for a doctor visit or school form.
- `follow-up-tracker` operation reads the follow-up callouts.
