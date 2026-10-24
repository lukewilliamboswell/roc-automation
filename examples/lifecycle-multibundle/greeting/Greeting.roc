import core.Answer

## The public interface exercised by the release consumer.
Greeting := [].{

	## The greeting composed from the sibling package value.
	message = "The answer is ${Answer.value.to_str()}"
}

expect Greeting.message == "The answer is 42"
