# Third-party documentation assets

The native `roc-docs` renderer embeds Inter, Merriweather and Fira Code from
its content-addressed roc-font 0.1.0-rc4 package under the SIL Open Font License
1.1. The package retains unchanged upstream static faces and reviewed provenance;
roc-docs uses its public named face, byte and family license accessors. The
website distributes all ten static faces with the three complete family notices.
Generated diagram SVGs embed the same Inter bytes used for measurements and
retain the complete Inter notice in metadata. Notices are also embedded in
`roc-docs --licenses`; the tools release obtains its license artifact from that
executable. No font preparation or duplicate font assets are maintained here.
The renderer was adapted from the UPL-1.0 roc-ansi documentation spike;
attribution is retained beside the source.

`roc-policy` embeds the MIT-licensed typos correction dictionary. Its exact
source version, license, provenance and offline digest verifier are in
`tools/roc-policy/vendor/typos/`. The permission notice is embedded in
`roc-policy --licenses` and included in the tools release.

The native PNG decoder adapts bounded inflation from the Apache-2.0 licensed
roc-deflate implementation. Its full license and adaptation notice are in
`tools/roc-docs/vendor/`, included in `roc-docs --licenses` and the tools release.
