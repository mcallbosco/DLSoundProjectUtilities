# Localization characterization fixture

The `expected/` files and `language_metadata.json` were generated on 2026-09-04
by `AllInOne.batch_gui.BatchGUI` at foundation commit `df149f7d`, before extracting
the localization implementation. Only `generated_at` and `source_directory` were
replaced with placeholders. JSON field, key, and array order is preserved.

The synthetic inputs exercise UTF-8 BOMs, Unicode and escaped quotes, both Tokens
block layouts, nested blocks, duplicate tokens, suffix precedence and collisions,
exact-key overrides, filename extension deduplication, ignored files, hero aliases
and collisions, canonical character order, name markup, and excluded hero tokens.

These files are a regression contract. Do not regenerate them from the extracted
implementation to make a failing test pass.
