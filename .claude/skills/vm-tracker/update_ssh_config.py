"""Point a ~/.ssh/config Host entry at a new IP address.

Usage: update_ssh_config.py HOST IP [--template HOST2] [--user USER] [--config PATH] [--dry-run]

Rewrites the ``HostName`` line of the ``Host HOST`` block (adding one if the
block has none), or appends a new block when HOST is not configured. With
``--template`` the new block is cloned from that host's block (e.g. ``station``).
A ``.bak`` copy is written before any change. Prints the old and new HostName.
"""

import argparse
import re
import shutil
from pathlib import Path


def block_bounds(lines: list[str], host: str) -> tuple[int, int] | None:
    """Return (start, end) line indexes of the ``Host`` block naming ``host``."""
    start = None
    for i, line in enumerate(lines):
        words = line.split()
        if words and words[0].lower() == "host" and host in words[1:]:
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"\s*(host|match)\s", lines[i], re.IGNORECASE):
            end = i
            break
    return start, end


def update(
    text: str, host: str, ip: str, user: str | None, template: str | None = None
) -> tuple[str, str | None]:
    """Return the updated config text and the previous HostName, if any."""
    lines = text.splitlines(keepends=True)
    bounds = block_bounds(lines, host)
    if bounds is None:
        tmpl = block_bounds(lines, template) if template else None
        if tmpl:
            body = lines[tmpl[0] + 1 : tmpl[1]]
            body = [ln for ln in body if not re.match(r"\s*hostname\s", ln, re.I)]
            block = f"\nHost {host}\n    HostName {ip}\n" + "".join(body).rstrip("\n") + "\n"
            sep = "" if not text or text.endswith("\n") else "\n"
            return text + sep + block, None
        block = f"\nHost {host}\n    HostName {ip}\n"
        if user:
            block += f"    User {user}\n"
        block += (
            "    StrictHostKeyChecking accept-new\n"
            "    ServerAliveInterval 30\n"
            "    ServerAliveCountMax 3\n"
        )
        sep = "" if not text or text.endswith("\n") else "\n"
        return text + sep + block, None

    start, end = bounds
    old = None
    for i in range(start + 1, end):
        m = re.match(r"(\s*)hostname(\s*=?\s*)(\S+)", lines[i], re.IGNORECASE)
        if m:
            old = m.group(3)
            lines[i] = f"{m.group(1)}HostName {ip}\n"
            break
    else:
        lines.insert(start + 1, f"    HostName {ip}\n")
    return "".join(lines), old


def main() -> None:
    """Parse arguments and rewrite the config file in place."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("host")
    p.add_argument("ip")
    p.add_argument("--user", help="User for a newly appended block (no template)")
    p.add_argument("--template", help="Clone this existing Host block for a new alias")
    p.add_argument("--config", type=Path, default=Path.home() / ".ssh" / "config")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    text = args.config.read_text() if args.config.exists() else ""
    new, old = update(text, args.host, args.ip, args.user, args.template)
    print(f"Host {args.host}: HostName {old or '(none)'} -> {args.ip}")
    if new == text or args.dry_run:
        return
    if args.config.exists():
        shutil.copy2(args.config, args.config.with_name(args.config.name + ".bak"))
    else:
        args.config.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.config.write_text(new)
    args.config.chmod(0o600)


if __name__ == "__main__":
    main()
