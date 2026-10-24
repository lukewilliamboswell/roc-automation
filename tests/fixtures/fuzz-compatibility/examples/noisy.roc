app [target] { fuzz: platform "https://github.com/lukewilliamboswell/roc-fuzz/releases/download/0.4.3/41NK5FShyGC9Z8HNpUdUeXMWwLmLNfFxsfmLvdPL4Fxb.tar.zst" }

import fuzz.Fuzz

## The negative probe must stop at the runner's existing output bound even
## though the case ignores stderr and would otherwise expect libFuzzer's 77.
target = Fuzz.from_bytes({ name: "compatibility-output-limit", test: |_bytes| crash Str.repeat("bounded-fuzz-output-", 1_000_000) })
