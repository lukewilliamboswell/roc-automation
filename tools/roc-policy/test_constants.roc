app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.25.0/CZsY7tYZwR3rj9kYbpaCfxki2yVAaRL8bBwMLvB2xkbA.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/2.1.0-rc.1/6dvNG3Q2mx53i4YPJN2ZGK98WdXnBMMuceAmDsCq7BEd.tar.zst",
}

import src/ConstantData
main! = |_| Ok({})

kind = |source| ConstantData.evaluate(source, "config").map_ok(|v| ConstantData.unwrap(v).kind)

error_kind = |source| match ConstantData.evaluate(source, "config") {
	Err(e) => e.kind
	Ok(_) => "unexpected success"
}

expect kind("config = (42)") == Ok(Integer("42"))
expect kind("config = True") == Ok(Boolean(True))
expect kind("config = Warn") == Ok(Tag("Warn"))
expect kind("config = \"hello\\nworld\"") == Ok(String("hello\nworld"))
expect kind("config = \"\\u(0)\\u(e9)\\u(1f642)\"") == Ok(String("\u(0)é🙂"))
expect kind("config = later.value\nlater = { value: \"yes\" }") == Ok(String("yes"))
expect kind("config = a\na = b\nb = 7") == Ok(Integer("7"))
expect kind("module [config]\nimport Foo\nhelper = |x| x + 1\nconfig = { version: \"v1\" }.version") == Ok(String("v1"))
expect {
	v = ConstantData.evaluate("shared = \"ok\"\nconfig = { shared, }", "config")?
	match ConstantData.unwrap(v).kind {
		Record([(key, item)]) => key == "shared" and ConstantData.unwrap(item).kind == String("ok")
		_ => False
	}
}
expect {
	v = ConstantData.evaluate("config = [1, False, Warn]", "config")?
	match ConstantData.unwrap(v).kind {
		ListValue(items) => items.map(|item| ConstantData.unwrap(item).kind) == [Integer("1"), Boolean(False), Tag("Warn")]
		_ => False
	}
}
expect error_kind("config = other") == "unknown"
expect error_kind("config = { a: 1 }.missing") == "unknown"
expect error_kind("config = config") == "cycle"
expect error_kind("config = a\na = config") == "cycle"
expect error_kind("config = {a: 1, a: 2}") == "duplicate"
expect error_kind("config = 1\nconfig = 2") == "duplicate"
expect error_kind("config = f(1)") == "unsupported"
expect error_kind("config = |x| x") == "unsupported"
expect error_kind("config = 1 + 2") == "unsupported"
expect error_kind("config = \"hi \${name}\"") == "unsupported"
expect error_kind("config = 1.5") == "unsupported"
expect error_kind("config = [") == "parse"
expect error_kind("config = 1\nunrelated = [") == "parse"
expect {
	match ConstantData.evaluate("config = missing", "config") {
		Err(e) => e.start == 9 and e.end == 16
		Ok(_) => False
	}
}
expect {
	var $declarations = []
	var $i = 0.U64
	while $i < 70 {
		$declarations = $declarations.append("x${$i.to_str()} = x${($i + 1).to_str()}")
		$i = $i + 1
	}
	source = "config = x0\n${Str.join_with($declarations, "\n")}\nx70 = 1"
	error_kind(source) == "limit"
}

expect kind("config = -42") == Ok(Integer("-42"))

expect kind("app [main!] { cli: platform \"https://example.invalid/platform.tar.zst\" }\nimport cli.Stdout\nmain! = |_| Ok({})\nconfig = { version: \"v1\" }.version") == Ok(String("v1"))
expect error_kind("config = { ..old, a: 1 }") == "unsupported"
expect error_kind("config = import_value.field") == "unknown"
expect {
	var $source = "x0 = [1]\n"
	var $i = 1.U64
	while $i <= 15 {
		previous = ($i - 1).to_str()
		$source = "${$source}x${$i.to_str()} = [x${previous}, x${previous}]\n"
		$i = $i + 1
	}
	error_kind("${$source}config = x15") == "limit"
}
