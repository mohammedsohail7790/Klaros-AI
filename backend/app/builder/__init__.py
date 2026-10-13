"""Klaros App Builder: requirements-driven, multi-page project generation.

Pipeline (each stage is a separate module, none of them runs generated code):

  spec.py          the validated, persisted ProjectSpec (sitemap, design brief, entities, features)
  planner.py       prompt -> ProjectSpec (LLM when connected, deterministic requirement extraction otherwise)
  workspace.py     a bounded, path-safe virtual project filesystem (create/read/patch/delete/rename/search)
  design.py        design directions -> tokens + the shared stylesheet
  generator.py     ProjectSpec -> real project files (HTML, CSS, JS, JSON data)
  validator.py     static build checks: routes, links, assets, accessibility, security
  orchestrator.py  explicit run states, bounded repair, snapshots, rollback, cancellation
  preview.py       signed, sandboxed serving of a version's files

Generated projects are STATIC (HTML/CSS/JS + JSON data). That is deliberate: the backend never executes
generated code, so there is nothing to escape from. Features that need a server (accounts, payments,
a database) are reported as such rather than faked.
"""
