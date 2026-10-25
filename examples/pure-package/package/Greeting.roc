## A small reusable module; package CI checks it with the current nightly.
Greeting := [].{
	## What the package says to everyone.
	greeting : Str
	greeting = "Hello from a pure Roc package"
}

expect Greeting.greeting.starts_with("Hello")
