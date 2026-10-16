app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/2.1.0-rc.1/6dvNG3Q2mx53i4YPJN2ZGK98WdXnBMMuceAmDsCq7BEd.tar.zst",
}
import src/TestAccounting
main! = |_| Ok({})

expected = TestAccounting.inventory("A.roc", "expect True".to_utf8()) ?? { path: "", source: "", tests: [], completed: False }

report : List(TestAccounting.FileInventory) -> Str
report = |files| Json.to_str({ schema_version: 1.U64, files })

passed = { ..expected, completed: True, tests: expected.tests.map(|test| { ..test, status: "passed" }) }
expect TestAccounting.inventory("A.roc", "expect True".to_utf8()).is_ok()
expect TestAccounting.inventory("A.roc", "A := [].{\nexpect True\n}".to_utf8()).map_ok(|file| file.tests.len()) == Ok(1)
expect TestAccounting.inventory("A.roc", "f = |_| {\nexpect True\n1\n}".to_utf8()).is_err()
expect TestAccounting.inventory("A.roc", "expect".to_utf8()).is_err()
expect TestAccounting.verify([expected], report([passed])) == Ok([])
expect TestAccounting.verify([expected], report([{ ..passed, source: "expect False" }])).map_ok(|found| found.len()) == Ok(1)
expect TestAccounting.verify([expected], report([])).map_ok(|found| found.len()) == Ok(1)
expect TestAccounting.verify([expected], report([{ ..passed, tests: [] }])).map_ok(|found| found.len()) == Ok(1)
expect TestAccounting.verify([expected], report([{ ..passed, completed: False }])).map_ok(|found| found.len()) == Ok(1)
expect TestAccounting.verify([expected], report([passed, passed])).is_err()
expect TestAccounting.verify([expected], report([{ ..passed, tests: passed.tests.concat(passed.tests) }])).is_err()
expect ["failed", "skipped", "pending"].all(|status| TestAccounting.verify([expected], report([{ ..passed, tests: passed.tests.map(|test| { ..test, status }) }])).map_ok(|found| found.len()) == Ok(1))
expect TestAccounting.verify([expected], report([{ ..passed, tests: passed.tests.map(|test| { ..test, status: "unknown" }) }])).is_err()
expect TestAccounting.verify([], report([])).is_err()
expect TestAccounting.verify([{ ..expected, tests: [] }], report([])).is_err()
expect TestAccounting.verify([expected], "{}").is_err()
expect TestAccounting.verify([expected], "{broken").is_err()
expect TestAccounting.verify([expected], "{\"schema_version\":2,\"files\":[]}").is_err()
expect TestAccounting.verify([expected], "{\"schema_version\":1,\"schema_version\":1,\"files\":[]}").is_err()
expect TestAccounting.verify([expected], "{\"schema_version\":1,\"files\":[],\"extra\":{\"a\":1,\"\\u0061\":2}}").is_err()
expect TestAccounting.summary_count("All (24) tests passed in 292.1 ms.\n") == Ok(24)
expect TestAccounting.summary_count("All (1) tests passed in 1 s.") == Ok(1)
expect ["", "All (1) tests passed in 1 ms. (cached)", "All (1) tests passed in 1 ms.\nAll (1) tests passed in 1 ms.", "All (1) tests passed in nope ms.", "All (-1) tests passed in 1 ms.", "All (18446744073709551616) tests passed in 1 ms.", "All (1) tests passed in 1 ms.\n1 failed"].all(|text| TestAccounting.summary_count(text).is_err())
expect TestAccounting.inventory("A.roc", "# expect False\ntext = \"expect False\"\nexpect True".to_utf8()).map_ok(|file| file.tests.len()) == Ok(1)
expect TestAccounting.inventory("A.roc", "expect {\nexpect True\nTrue\n}".to_utf8()).is_err()
expect TestAccounting.verify([expected, expected], report([passed])).is_err()
expect TestAccounting.verify([expected], report([{ ..passed, path: "B.roc" }])).map_ok(|found| found.any(|issue| issue.message == "unexpected file in runner report")) == Ok(True)
expect TestAccounting.verify([expected], report([{ ..passed, tests: [{ start: 1.U64, end: 11.U64, status: "passed" }] }])).map_ok(|found| found.len()) == Ok(2)
expect TestAccounting.verify([expected], report([{ ..passed, tests: [{ start: 0.U64, end: 99.U64, status: "passed" }] }])).is_err()
expect TestAccounting.verify([expected], report([{ ..passed, tests: [{ start: 1.U64, end: 1.U64, status: "passed" }] }])).is_err()
expect TestAccounting.verify([expected], "{\"schema_version\":1,\"files\":[],\"extra\":{\"a\":1,\"nested\":{\"a\":2}}}").is_ok()
expect TestAccounting.verify([expected], "{\"schema_version\":1,\"files\":[],\"extra\":\"{\\\"a\\\":1,\\\"a\\\":2}\"}").is_ok()
expect TestAccounting.verify([expected], "{\"schema_version\":1,\"files\":[],\"extra\":[{\"a\":1,\"a\":2}]}").is_err()
expect TestAccounting.verify([expected], "{\"schema_version\":1,\"files\":[],\"extra\":{\"a\\\"b\":1,\"a\\\"b\":2}}").is_err()
expect TestAccounting.summary_count("\u(001b)[32mAll (1) tests passed in 1 ms.").is_err()
expect TestAccounting.summary_count("All (1) tests passed in 1.2.3 ms.").is_err()
expect TestAccounting.summary_count("All (1) tests passed in 1 ms. extra").is_err()
expect TestAccounting.summary_count("All (18446744073709551615) tests passed in 1 ms.") == Ok(18446744073709551615)
expect TestAccounting.inventory("A.roc", "# café\nexpect True".to_utf8()).map_ok(|file| file.tests.map(|test| (test.start, test.end))) == Ok([(8, 19)])
