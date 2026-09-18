"""Subcommand group implementations for the CLI.

Each module exposes:

- ``add_parser(subparsers)``: register a top-level subcommand group;
- ``run(args, stdout, stderr)``: execute the selected subcommand (stdout and
  stderr are ``TextIO`` streams) and return a process exit code
  (0 = success, 1 = runtime failure, 2 = usage error).
"""
