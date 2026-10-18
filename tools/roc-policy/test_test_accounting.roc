app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/2.1.0-rc.1/6dvNG3Q2mx53i4YPJN2ZGK98WdXnBMMuceAmDsCq7BEd.tar.zst",
}
import src/TestAccounting
main! = |_| Ok({})

expected = TestAccounting.inventory("A.roc", "expect True".to_utf8()) ?? { path: "", source: "", tests: [] }

locations = "PASS: /root/A.roc:1\nAll (1) tests passed in 1 ms."
expect TestAccounting.inventory("A.roc", "expect True".to_utf8()).is_ok()
expect TestAccounting.inventory("A.roc", "A := [].{\nexpect True\n}".to_utf8()).map_ok(|file| file.tests.len()) == Ok(1)
expect TestAccounting.inventory("A.roc", "f = |_| {\nexpect True\n1\n}".to_utf8()).map_ok(|file| file.tests.len()) == Ok(0)
expect TestAccounting.inventory("A.roc", "expect".to_utf8()).is_err()
expect TestAccounting.inventory("A.roc", "# expect False\ntext = \"expect False\"\nexpect True".to_utf8()).map_ok(|file| file.tests.len()) == Ok(1)
expect TestAccounting.inventory("A.roc", "expect {\nexpect True\nTrue\n}".to_utf8()).map_ok(|file| file.tests.len()) == Ok(1)
expect TestAccounting.inventory("A.roc", "expect { expect True\n True }".to_utf8()).is_err()
expect TestAccounting.inventory("A.roc", "A := [].{ expect True\n expect True }".to_utf8()).is_ok()
expect TestAccounting.inventory("A.roc", "# café\nexpect True".to_utf8()).map_ok(|file| file.tests.map(|test| (test.start, test.end))) == Ok([(8, 19)])
expect TestAccounting.summary_count("All (24) tests passed in 292.1 ms.\n") == Ok(24)
expect TestAccounting.summary_count("All (1) tests passed in 1 s.") == Ok(1)
expect TestAccounting.summary_count("All (3) tests passed in 1 ms. (cached)") == Ok(3)
expect TestAccounting.summary_count("All (18446744073709551615) tests passed in 1 ms.") == Ok(18446744073709551615)
expect ["", "All (1) tests passed in 1 ms.\nAll (1) tests passed in 1 ms.", "All (1) tests passed in nope ms.", "All (-1) tests passed in 1 ms.", "All (18446744073709551616) tests passed in 1 ms.", "All (1) tests passed in 1 ms.\n1 failed", "\u(001b)[32mAll (1) tests passed in 1 ms.", "All (1) tests passed in 1.2.3 ms.", "All (1) tests passed in 1 ms. extra"].all(|text| TestAccounting.summary_count(text).is_err())
expect TestAccounting.verify_locations(expected, "/root/A.roc", locations) == Ok([])
expect TestAccounting.verify_locations(expected, "/root/A.roc", "${locations} (cached)") == Ok([])
expect TestAccounting.verify_locations(expected, "/root/A.roc", "PASS: /root/B.roc:1\nPASS: /root/A.roc:1\nAll (2) tests passed in 1 ms.") == Ok([])
expect TestAccounting.verify_locations(expected, "/root/A.roc", "PASS: /root/A.roc:1\nPASS: /root/A.roc:1\nAll (2) tests passed in 1 ms.").map_ok(List.len) == Ok(1)
expect TestAccounting.verify_locations(expected, "/root/A.roc", "PASS: /root/B.roc:1\nAll (1) tests passed in 1 ms.").map_ok(List.len) == Ok(1)
expect TestAccounting.verify_locations(expected, "/root/A.roc", "All (0) tests passed in 1 ms.").map_ok(List.len) == Ok(1)
expect TestAccounting.verify_locations(expected, "/root/A.roc", "All (1) tests passed in 1 ms.").is_err()
expect TestAccounting.verify_locations(expected, "A.roc", locations).is_err()
expect TestAccounting.verify_locations(expected, "/root/A.roc", "PASS: /root/A.roc:nope\nAll (1) tests passed in 1 ms.").is_err()
expect TestAccounting.verify_locations(expected, "/root/A.roc", "PASS: /root/A.roc:0\nAll (1) tests passed in 1 ms.").is_err()
expect TestAccounting.verify_locations(expected, "/root/A.roc", "PASS: A.roc:1\nAll (1) tests passed in 1 ms.").is_err()
expect TestAccounting.verify_locations(expected, "/root/A.roc", "PASS: /root/A.roc:1").is_err()
expect TestAccounting.verify_locations({ ..expected, tests: [] }, "/root/A.roc", locations).is_err()
expect TestAccounting.verify_locations(expected, "/root/colon:A.roc", "PASS: /root/colon:A.roc:1\nAll (1) tests passed in 1 ms.") == Ok([])
expect TestAccounting.verify_locations(expected, "/root/errors.roc", "PASS: /root/errors.roc:1\nAll (1) tests passed in 1 ms.") == Ok([])
expect {
	inv = TestAccounting.inventory("A.roc", "helper = |_| {\nexpect True\nTrue\n}\nexpect helper({})".to_utf8())?
	TestAccounting.verify_locations(inv, "/root/A.roc", "PASS: /root/A.roc:2\nPASS: /root/A.roc:5\nPASS: /root/B.roc:1\nAll (3) tests passed in 1 ms. (cached)") == Ok([])
}
expect {
	inv = TestAccounting.inventory("A.roc", "# café\r\nexpect True".to_utf8())?
	TestAccounting.verify_locations(inv, "/root/A.roc", "PASS: /root/A.roc:2\nAll (1) tests passed in 1 ms.") == Ok([])
}
