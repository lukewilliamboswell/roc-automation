"""Inert child-process fixture used through native launchers on Windows."""
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(newline="\n")
if os.environ.get("FAKE_CASE_SIGNAL"):
    import signal
    os.kill(os.getpid(), signal.SIGTERM)
print("hello", flush=True)
if os.environ.get("FAKE_CHILD_MARKER"):
    subprocess.Popen([sys.executable, "-c",
                      "import time,pathlib;time.sleep(1.5);pathlib.Path("
                      + repr(os.environ["FAKE_CHILD_MARKER"]) + ").write_text('escaped')"])
if os.environ.get("FAKE_CASE_FLOOD"):
    while True:
        os.write(sys.stdout.fileno(), b"x" * 65536)
if os.environ.get("FAKE_CASE_SLEEP"):
    time.sleep(30)

sys.exit(int(os.environ.get("FAKE_CASE_EXIT", "0")))
