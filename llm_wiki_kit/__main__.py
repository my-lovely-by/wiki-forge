"""Allow ``python -m llm_wiki_kit`` to invoke the CLI."""

from llm_wiki_kit.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
