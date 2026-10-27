app [answer, main!] { pf: platform "../platform/main.roc" }

import pf.Contract

answer : I64
answer = Contract.expected

main! = || {}
