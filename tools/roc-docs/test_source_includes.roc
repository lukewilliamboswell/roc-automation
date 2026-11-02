app [main!] {
	cli: platform "https://github.com/roc-lang/basic-cli/releases/download/0.26.0-rc2/Gj3zVDr3qrQa6aTh7Z1zgUfe9NeBE6R76T5ho3xrc8af.tar.zst",
	parser: "https://github.com/lukewilliamboswell/roc-parser/releases/download/3.0.0/6xsRdRqenJrAb4wG7HaST4Xh349vD5sHAJDsKTK6E3dr.tar.zst",
}
import SourceIncludes
import Destination
import "tests/fixtures/compatibility/csv-guide.expected" as csv_output : Str
main! = |_| Ok({})

expect SourceIncludes.select("value = 1  \n", "") == Ok("value = 1  \n")
expect SourceIncludes.select("header\n# tag::body[]\n  value = 1  \n# end::body[]\n", "tag=body,indent=0") == Ok("value = 1  \n")
expect SourceIncludes.select("# tag::body[]\nvalue\n", "tag=body").is_err()
expect SourceIncludes.select("value\n", "tag=missing").is_err()
expect SourceIncludes.select("value\n", "tag=a,tag=b").is_err()
expect SourceIncludes.select("value\r\n", "").is_err()
expect Destination.encode("café") == "caf%C3%A9"
expect Destination.encode("caf%C3%A9") == "caf%25C3%25A9"

## Public selection preserves checked output without adding source tag markers.
expect SourceIncludes.select(csv_output, "lines=2") == Ok("nut: 40 at 0.05\n")
expect SourceIncludes.select(csv_output, "lines=1..4,indent=0") == Ok(csv_output)
expect SourceIncludes.select("\tfirst  \n  second\nlast", "lines=1..2,indent=0") == Ok("first  \n second\n")
expect SourceIncludes.select("first\nsecond", "lines=2..2") == Ok("second\n")
expect ["lines=0", "lines=-1", "lines=+1", "lines=", "lines=1..", "lines=..2", "lines=1..-1", "lines=2..1", "lines=1;2", "lines=1,2", "lines=1abc", "lines=1...2", "lines=3", "lines=1..3", "lines=18446744073709551616", "lines=1,lines=2", "tag=body,lines=1", "lines=1,tag=body", "lines=1,indent=1", "lines=1,indent=0,indent=0", "lines=1,"].all(|settings| SourceIncludes.select("first\nsecond\n", settings).is_err())
expect SourceIncludes.select("# tag::body[]\none\n# end::body[]\n# tag::body[]\ntwo\n# end::body[]\n", "tag=body").is_err()
expect SourceIncludes.select("# tag::body[]\n# tag::body[]\none\n# end::body[]\n# end::body[]\n", "tag=body").is_err()
expect SourceIncludes.select("", "lines=1").is_err()

## Line selection must not change the legacy tag byte contract: unrelated
## markers are source content, and whitespace-only lines do not set dedent width.
expect SourceIncludes.select("# tag::body[]\n  keep\n  # tag::other[]\n  inner\n \n  # end::other[]\n  end\n# end::body[]\n", "tag=body,indent=0") == Ok("keep\n# tag::other[]\ninner\n\n# end::other[]\nend\n")
expect SourceIncludes.select("# tag::body[]\n# end::body[]\n", "tag=body") == Ok("\n")
