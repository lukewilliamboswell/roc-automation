app [answer, main!] { pf: platform "../platform/main.roc" }

## The host must reject a changed application value.
answer : I64
answer = 41

main! = || {}
