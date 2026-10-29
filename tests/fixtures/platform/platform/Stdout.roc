import Host

## Write to standard output.
Stdout := [].{
	line! : Str => Try({}, [StdoutErr(Str)])
	line! = |message| Host.stdout_line!(message)
}
