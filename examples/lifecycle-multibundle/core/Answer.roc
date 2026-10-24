## The public interface exercised by the release consumer.
Answer := [].{

	## The public value imported by the dependent bundle.
	value : I64
	value = 42
}

expect Answer.value == 42
