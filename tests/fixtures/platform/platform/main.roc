platform ""
    requires {} { main! : List(Str) => Try({}, _) }
    exposes [Stdout]
    packages {}
    provides { "roc_main": main_for_host! }
    hosted {
        "roc_stdout_line": Host.stdout_line!,
    }
    targets: {
        inputs_dir: "targets/",
        x64musl: { inputs: ["crt1.o", "libhost.a", app, "libc.a"] },
    }

import Stdout
import Host

main_for_host! : List(Str) => I32
main_for_host! = |args| {
    match main!(args) {
        Ok({}) => 0
        Err(_) => 1
    }
}
