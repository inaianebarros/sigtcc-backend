"""Holds the personas instance lock in its own process until its standard input receives a line.

Run by the lock test as `python -m tests.personas.chaos.lock_holder`. It prints LOCKED once it
holds the lock, or BUSY if another process already did.
"""

import sys

import django


def main() -> None:
    django.setup()
    from apps.personas.cycle import instance_lock

    with instance_lock() as acquired:
        print("LOCKED" if acquired else "BUSY", flush=True)
        if acquired:
            sys.stdin.readline()


if __name__ == "__main__":
    main()
