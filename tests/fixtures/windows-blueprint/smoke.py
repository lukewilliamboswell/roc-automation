"""Executed through Blueprint, with no Nix or ambient Roc compiler."""
import os
import subprocess
from pathlib import Path

assert os.name == "nt", "this fixture must run natively on Windows"
assert Path("Blueprint.lock").is_file()
assert subprocess.check_output(["blueprint", "--version"], text=True).strip() == "0.8.0"
print("Blueprint native Windows task passed")
