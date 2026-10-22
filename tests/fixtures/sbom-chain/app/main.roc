app [main!] { cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.26.0-rc2/Gj3zVDr3qrQa6aTh7Z1zgUfe9NeBE6R76T5ho3xrc8af.tar.zst" }

import cli.Stdout

## A native executable; its SBOM is bound per target.
main! = |_args| Stdout.line!("Hello from a native binary with an SBOM")
