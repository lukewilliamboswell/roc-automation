## Internal hosted-effect boundary used by the platform wrappers.
Host := [].{
	stdout_line! : Str => Try({}, [StdoutErr(Str)])
}
