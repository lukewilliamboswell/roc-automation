# roc-automation tools 0.6.0

This release updates `roc-docs` to keep PDF contents page numbers aligned at
the right margin, with dotted leaders joining each heading to its page number.
Nested entries retain their indentation and linked destinations. The renderer
uses roc-pdf 0.2.0-rc1's contents entries and measures the resulting layout
after pagination.

Browser regression coverage checks source listings in light and dark themes,
including whitespace, token backgrounds and horizontal scrolling. PDF review
captures cover the contents layout alongside the HTML checks.

The native utilities keep their existing command-line interfaces. Consumers
select the executable for their system and verify it against the release's
`SHA256SUMS`; Blueprint consumers record that digest in `Blueprint.lock`.
