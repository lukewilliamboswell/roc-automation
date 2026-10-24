import core.Answer

Greeting := [].{
	message = "The answer is ${Answer.value.to_str()}"
}

expect Greeting.message == "The answer is 42"
