import cli.Cmd
import cli.Path
import cli.Tcp

## Serves one package bundle from 127.0.0.1.
##
## Roc fetches a package dependency by URL while it compiles. Serving the exact
## archive a release would publish lets the archived examples be tested against
## it, with `--replace-dep`, before any release exists.
Loopback := [].{

	## A running server: what `run!` answers requests with, and the URL that
	## stands in for the release URL while it runs.
	Server : { listener : Tcp.Listener, bytes : List(U8), url : Str }

	## Read `bundle` and start listening on a free port.
	start! : Path => Try(Server, Str)
	start! = |bundle| {
		bytes = Path.read_bytes!(bundle).map_err(|_| "cannot read the bundle to serve")?
		file_name = Path.filename(bundle).map_ok(Path.display).map_err(|_| "the bundle has no file name")?
		listener = Tcp.listen!("127.0.0.1", 0, 5_000).map_err(|_| "cannot listen on a loopback port")?
		port = listener.local_port!().map_err(|_| "cannot read the loopback port")?
		Ok({ listener, bytes, url: "http://127.0.0.1:${port.to_str()}/${file_name}" })
	}

	## Run `command` to completion with captured output, answering every
	## connection made in the meantime with the bundle. The command's own
	## timeout and output limit still apply, and end the run as they would
	## under `Cmd.run!`; a failure of the server itself is `Serve`.
	run! = |server, command| {
		child = command.stdout(Capture).stderr(Capture).spawn!().map_err(|err| IO(err))?
		serve_until_exit!(child, server)
	}

	## Stop listening.
	stop! : Server => Try({}, Str)
	stop! = |server| server.listener.close!().map_err(|_| "cannot close the loopback listener")
}

# This process must answer the child's request and notice the child exiting,
# without threads. A short accept timeout alternates between the two.
serve_until_exit! = |child, server|
	match child.try_wait!() {
		Ok([output, ..]) => Ok(output)
		Ok([]) =>
			match server.listener.accept!(100) {
				Ok(stream) => {
					_ = stream.read_until!('\n', 65_536, 5_000).map_err(|_| Serve("cannot read the request"))?
					stream.write_utf8!(response_headers(server.bytes.len()), 5_000).map_err(|_| Serve("cannot write the response headers"))?
					stream.write!(server.bytes, 30_000).map_err(|_| Serve("cannot write the bundle"))?
					serve_until_exit!(child, server)
				}
				Err(TcpListenErr(TimedOut)) => serve_until_exit!(child, server)
				Err(_) => Err(Serve("cannot accept a connection"))
			}
		Err(Timeout(partial)) => Err(Timeout(partial))
		Err(OutputLimit(partial)) => Err(OutputLimit(partial))
		Err(IO(err)) => Err(IO(err))
	}

# HTTP separates header lines with CRLF.
response_headers = |content_length|
	Str.join_with(
		[
			"HTTP/1.1 200 OK",
			"Content-Type: application/octet-stream",
			"Content-Length: ${content_length.to_str()}",
			"Connection: close",
			"",
			"",
		],
		"\r\n",
	)

expect response_headers(12) == "HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Length: 12\r\nConnection: close\r\n\r\n"
