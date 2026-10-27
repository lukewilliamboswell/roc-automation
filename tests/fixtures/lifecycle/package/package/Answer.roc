## A pure public API with an independently owned product unit test.
Answer := [].{

	## The public answer used by the released example.
	value : I64
	value = 42
}

expect Answer.value == 42
