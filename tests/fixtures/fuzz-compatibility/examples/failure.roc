app [target] { fuzz: platform "https://github.com/lukewilliamboswell/roc-fuzz/releases/download/0.4.3/41NK5FShyGC9Z8HNpUdUeXMWwLmLNfFxsfmLvdPL4Fxb.tar.zst" }

import fuzz.Fuzz

## Every seed reproduces this deliberate failure; no search luck is involved.
target = Fuzz.from_bytes({ name: "compatibility-failure", test: |_bytes| crash "deliberate compatibility fixture failure" })
