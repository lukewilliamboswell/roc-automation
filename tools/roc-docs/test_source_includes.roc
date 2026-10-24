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

# Checked output cannot carry tag markers. Select its actual lines instead.
expect SourceIncludes.select("one\ntwo  \n\tthree\nfour\n", "lines=2..3") == Ok("two  \n\tthree\n")
expect SourceIncludes.select("one\ntwo\nthree\n", "lines=2") == Ok("two\n")
expect SourceIncludes.select("header\n  one  \n  two\n", "lines=2..3,indent=0") == Ok("one  \ntwo\n")
expect SourceIncludes.select("one\ntwo\nthree\n", "lines=1;3") == Ok("one\nthree\n")
expect SourceIncludes.select("one\ntwo\n", "lines=2..-1") == Ok("two\n")
expect SourceIncludes.select("one\ntwo\n", "lines=2..1").is_err()
expect SourceIncludes.select("one\ntwo\n", "lines=3").is_err()
expect SourceIncludes.select("one\ntwo\n", "lines=1;3").is_err()
expect SourceIncludes.select("one\ntwo\n", "lines=1abc").is_err()
expect SourceIncludes.select("one\ntwo\n", "lines=").is_err()
expect SourceIncludes.select("one\ntwo\n", "lines=1,lines=2").is_err()
expect SourceIncludes.select("one\ntwo\n", "lines=1,tag=missing").is_err()
expect SourceIncludes.select("", "").is_err()
expect SourceIncludes.select("# end::body[]\nvalue\n", "tag=body").is_err()
expect SourceIncludes.select("# tag::body[]\nvalue\n# tag::body[]\nagain\n# end::body[]\n", "tag=body").is_err()
