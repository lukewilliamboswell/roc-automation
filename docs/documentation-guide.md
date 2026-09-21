# Write project documentation

Use this guide when creating or reviewing documentation for a Roc package,
platform, application, or shared tool. It separates the reader's needs from the
project evidence that maintainers must preserve.

## Give each page one purpose

Choose the page's dominant purpose before writing it. The four Diátaxis forms
provide a useful test:

| Form | Reader need | Shape |
| --- | --- | --- |
| Tutorial | Learn by completing a guided first experience | A safe, linear path with a visible result |
| How-to guide | Accomplish a real task | Goal-oriented steps that assume relevant knowledge |
| Reference | Look up exact facts | Complete, neutral descriptions organized like the interface |
| Explanation | Understand context, design, or tradeoffs | Connected reasoning that answers why |

A page can link to another form, but should not interrupt a tutorial with an
exhaustive option catalogue or turn reference material into a narrated lesson.
Use one topic for one subject or reader question. Split a page when its sections
serve different audiences or outcomes.

The manual's reading order may still follow a natural journey: context, first
success, goal-oriented guides, reference, contributor material, then appendices.
The four forms are an editing discipline rather than required top-level folders.

## Define the page before drafting

Record these answers in the issue, pull request, or draft notes:

- Who is the intended reader?
- What do they already know and have installed?
- What question will this page answer?
- Which documentation form fits that question?
- What should the reader be able to do or understand afterward?
- Which commands, interfaces, versions, and platforms constrain the answer?

Lead with the outcome. Keep prerequisites close to the first step. Use complete,
copyable examples and explain the observable result. Link to detailed reference
or background when it would distract from the page's purpose.

## Show structure and behavior

Use a Mermaid diagram when relationships, state changes, trust boundaries, data
flow, or a multi-stage process would take several paragraphs to reconstruct. A
diagram should make one point and sit beside the prose that explains its meaning.
Prefer stable concepts over incidental implementation details so it remains
useful as the code changes.

In AsciiDoc sources built by `actions/build-docs`, embed Mermaid source so the
same diagram is rendered into the HTML site and PDF:

```asciidoc
[mermaid]
....
flowchart LR
    Source[Roc target] --> Build[roc build --fuzz]
    Build --> Runner[Self-contained runner]
    Runner --> Corpus[Coverage corpus]
    Runner --> Failure[Reproducing failure]
....
```

Give every diagram a descriptive title or nearby textual explanation. Do not put
information only in color, and make labels meaningful when printed in grayscale.
Keep the Mermaid source readable because it is also the maintainable form of the
diagram.

Use syntax-highlighted source blocks for commands, configuration, source code,
schemas, reports, and output. Name the language explicitly and keep examples
focused enough to scan:

```asciidoc
[source,roc]
----
target = Fuzz.target({ name: "parser", test, show })
----
```

Prefer examples that CI compiles or exercises. When a snippet is intentionally
incomplete, say what was omitted. Separate commands from their output, label both,
and never rely on highlighting alone to communicate an error or required change.

## Cover the public project experience

Review the documentation as a connected entry path, not only as individual
pages. A user should be able to find:

- a concise statement of the problem the project solves and its supported scope;
- how to obtain a released version and verify any required toolchain;
- a minimal working example with expected output or behavior;
- task-oriented guidance for normal operation and common failures;
- reference for every public input, output, command, option, configuration value,
  file format, and API;
- compatibility, support, and known limitation information;
- where to report bugs, request enhancements, discuss changes, and privately
  report vulnerabilities;
- how to contribute, including the change process, standards, tests, and checks;
- human-readable release notes and upgrade impact; and
- license, third-party notices, provenance, and acknowledgments where applicable.

These subjects need not all live in the manual. Keep short repository entry
points such as `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, and `LICENSE` where
hosting services and automated tools expect them, and link them to the detailed
manual where useful.

## Keep claims testable

Documentation is evidence of a declared process, not proof that the process is
followed. For every operational claim, identify how it is checked:

| Claim | Useful evidence |
| --- | --- |
| The documented build works | A clean CI job runs the same command |
| Public examples work | CI tests their committed release URLs without rewriting them |
| The test suite is the contribution gate | Contributor instructions and required CI run the stated command |
| The external interface is documented | An API or schema audit fails when public entries are missing |
| Releases are reproducible and identifiable | Immutable tags, artifact digests, provenance, and release notes |
| Vulnerability reports receive timely responses | A working private channel and periodically reviewed response history |

Prefer generated reference material only when generation is complete, readable,
and checked. Handwritten explanations and examples still need review when the
interface changes.

## Review each change

Before merging documentation:

- verify links, commands, examples, and expected results;
- build every published format, including HTML, PDF, and generated API pages;
- inspect Mermaid diagrams and highlighted code in both HTML and PDF output;
- check headings and links without relying on generated numbering;
- confirm the navigation names describe reader goals or subjects;
- check that warnings, limitations, and destructive steps appear before the
  action they qualify;
- review accessibility of diagrams, tables, link text, and color use;
- remove stale version claims and resolved temporary limitations; and
- update the project evidence inventory when a URL or process changes.

The [project-practice evidence guide](openssf.md) supplies the companion checklist
for repository and operational evidence. The
[Diátaxis framework](https://diataxis.fr/) and
[DITA topic guidance](https://docs.oasis-open.org/dita/v1.0/archspec/topicover.html)
are the sources for the content-shape principles used here.
