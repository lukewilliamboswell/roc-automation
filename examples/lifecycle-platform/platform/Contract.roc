## The public interface exercised by the release consumer.
Contract := [].{

	## The value the native host must receive through the public interface.
	expected : I64
	expected = 42
}

expect Contract.expected == 42
