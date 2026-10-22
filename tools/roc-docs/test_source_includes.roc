app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.26.0-rc2/Gj3zVDr3qrQa6aTh7Z1zgUfe9NeBE6R76T5ho3xrc8af.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/3.0.0/6xsRdRqenJrAb4wG7HaST4Xh349vD5sHAJDsKTK6E3dr.tar.zst",
}
import SourceIncludes
import Destination
main! = |_| Ok({})

expect SourceIncludes.select("value = 1  \n", "") == Ok("value = 1  \n")
expect SourceIncludes.select("header\n# tag::body[]\n  value = 1  \n# end::body[]\n", "tag=body,indent=0") == Ok("value = 1  \n")
expect SourceIncludes.select("# tag::body[]\nvalue\n", "tag=body").is_err()
expect SourceIncludes.select("value\n", "tag=missing").is_err()
expect SourceIncludes.select("value\n", "tag=a,tag=b").is_err()
expect SourceIncludes.select("value\r\n", "").is_err()
expect Destination.encode("café") == "caf%C3%A9"
expect Destination.encode("caf%C3%A9") == "caf%25C3%25A9"
