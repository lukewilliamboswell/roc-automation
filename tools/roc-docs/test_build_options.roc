app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.26.0-rc2/Gj3zVDr3qrQa6aTh7Z1zgUfe9NeBE6R76T5ho3xrc8af.tar.zst",
}
import BuildOptions
import BuildFiles
import cli.Path
main! = |_args| Ok({})
expect match BuildOptions.parse(["build"]) {
	Ok(o) => o.pdf_image_dpi == 144 and o.output_directory == ".docs-out"
	Err(_) => False
}
expect match BuildOptions.parse(["prepare"]) {
	Ok(o) => o.output_directory == ".docs-prepared"
	Err(_) => False
}
expect ["../docs", "/docs", "a/../../docs", "a\\docs", ".git/docs", "docs\nother", "a//docs", "a/./docs", "https:docs"].all(|path| BuildOptions.parse(["build", "--docs-directory", path]).is_err())
expect ["docs", "docs/out", "docs/sub/out"].all(|path| BuildOptions.parse(["build", "--output-directory", path]).is_err())
expect BuildOptions.parse(["build", "--output-directory", "out", "--docs-directory", "out/docs"]).is_err()
expect BuildOptions.parse(["build", "--output-directory", "out", "--api-entrypoint", "out/main.roc"]).is_err()
expect ["71", "301", "-1", "144.5", "abc"].all(|dpi| BuildOptions.parse(["build", "--pdf-image-dpi", dpi]).is_err())
expect BuildOptions.parse(["build", "--title", "one", "--title", "two"]).is_err()
expect BuildOptions.parse(["build", "--unknown", "value"]).is_err()
expect BuildOptions.parse(["build", "--title"]).is_err()
expect BuildOptions.parse(["build", "--entrypoint", "nested/index.adoc"]).is_err()
expect BuildOptions.parse(["prepare", "--api-entrypoint", "main.roc"]).is_err()
expect BuildOptions.parse(["build", "--language", "en-AU"]).is_ok()
expect BuildOptions.parse(["build", "--author", "bad\nvalue"]).is_err()

expect BuildFiles.parent_path(Path.utf8("/project/out")) == Path.utf8("/project")
expect BuildOptions.parse(["build", "--site-url", "https://example.com/manual/"]).is_ok()
expect ["http://example.com/", "javascript:alert(1)", "https://example.com/#fragment", "https://example.com/?query", "https://example.com/ bad"].all(|url| BuildOptions.parse(["build", "--site-url", url]).is_err())

expect match BuildOptions.parse(["build", "--markdown", "true"]) {
	Ok(o) => o.markdown
	Err(_) => False
}
expect match BuildOptions.parse(["build"]) {
	Ok(o) => o.markdown
	Err(_) => False
}
expect ["yes", "TRUE", "", "false; echo injected"].all(|value| BuildOptions.parse(["build", "--markdown", value]).is_err())

expect BuildOptions.parse(["prepare", "--markdown", "true"]).is_err()

expect match BuildOptions.parse(["build", "--markdown", "false"]) {
	Ok(o) => !o.markdown
	Err(_) => False
}
expect match BuildOptions.parse(["prepare"]) {
	Ok(o) => !o.markdown
	Err(_) => False
}
