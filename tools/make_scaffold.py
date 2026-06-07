import base64, subprocess, datetime, textwrap

files = subprocess.check_output(
    ["git", "ls-files", "oracle_citadel", "control_panel",
     ".github/workflows/build-oracle-windows.yml"]).decode().split()

blob = {}
for f in files:
    with open(f, "rb") as fh:
        blob[f] = base64.b64encode(fh.read()).decode()

header = '#!/usr/bin/env python3\n'
doc = textwrap.dedent('''\
    """Oracle project scaffolder + guided installer — the whole stack from one file.

    Recreate the project and (optionally) generate your .env files interactively:

        python build_oracle_project.py            # write files, then offer guided setup
        python build_oracle_project.py --dest myapp
        python build_oracle_project.py --force    # overwrite existing files
        python build_oracle_project.py --configure # ONLY run the guided .env setup
        python build_oracle_project.py --no-setup  # write files, skip the .env wizard

    It writes:
        oracle_citadel/    - the MT5/NinjaTrader auto-trader + Windows packaging
        control_panel/     - the website control panel (Flask + Docker + Caddy)
        .github/workflows/ - the Build & Publish Oracle CI

    The guided setup prompts for your secrets, auto-generates strong passphrases,
    and writes oracle_citadel/.env and control_panel/.env (chmod 600). The shared
    control passphrase + data relay token are written identically to BOTH files,
    which is required for the bot and panel to talk. Secrets are never embedded in
    this script. Standard library only. Generated on {date}.
    """
''').format(date=datetime.date.today().isoformat())

# NOTE: this body is written verbatim into the generated file (no .format), so
# the f-strings and {braces} below are literal code, not template fields.
body = textwrap.dedent('''
    import argparse, base64, os, sys, secrets, getpass


    def _gen_secret(n=24):
        return secrets.token_urlsafe(n)


    def _prompt(label, default="", secret=False, optional=False):
        hint = f" [{default}]" if default else (" [optional]" if optional else "")
        while True:
            try:
                if secret and not default:
                    val = getpass.getpass(f"{label}{hint}: ").strip()
                else:
                    val = input(f"{label}{hint}: ").strip()
            except EOFError:
                val = ""
            if not val:
                val = default
            if not val and not optional:
                print("  (this one is required)")
                continue
            return val


    def _prompt_secret(label):
        """Return a typed value, or auto-generate a strong one on empty input."""
        try:
            raw = input(f"{label} [Enter = auto-generate]: ").strip()
        except EOFError:
            raw = ""
        return raw or _gen_secret()


    def _write_env(path, pairs, force):
        if os.path.exists(path) and not force:
            try:
                ans = input(f"  {path} exists - overwrite? [y/N]: ").strip().lower()
            except EOFError:
                ans = ""
            if ans != "y":
                print("  kept existing", path)
                return
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as fh:
            for k, v in pairs:
                fh.write(f"{k}={v}\\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        print("  wrote", path, "(chmod 600)")


    def run_guided_setup(dest, force):
        if not sys.stdin.isatty():
            print("\\n[setup] Not an interactive terminal - skipping guided .env setup.")
            print("        Run again with --configure in a real terminal, or create the")
            print("        .env files by hand (see oracle_citadel/README.md and")
            print("        control_panel/README.md).")
            return

        print("\\n=== Guided setup: creating your .env files ===")
        print("Press Enter to accept the [default] or auto-generate a secret.\\n")

        admin_user = _prompt("Operator username", default="admin")
        admin_pass = _prompt("Operator password", secret=True)

        tg_token = _prompt("Telegram bot token", optional=True)
        tg_chat = _prompt("Telegram chat ID", optional=True) if tg_token else ""

        webhook_pass = _prompt_secret("Webhook passphrase")
        control_pass = _prompt_secret("Control passphrase (shared with the panel)")
        relay_token = _prompt_secret("Data relay token (shared with the panel)")

        site_base = _prompt("Site base URL", default="https://www.alphadomain.space")
        wh_host = _prompt("Webhook bind host", default="0.0.0.0")
        wh_port = _prompt("Webhook bind port", default="80")

        default_domain = site_base.replace("https://", "").replace("http://", "").rstrip("/")
        panel_domain = _prompt("Panel domain", default=default_domain)
        panel_pass = _prompt_secret("Panel page password")

        bot_env = os.path.join(dest, "oracle_citadel", ".env")
        panel_env = os.path.join(dest, "control_panel", ".env")

        _write_env(bot_env, [
            ("ORACLE_ADMIN_USER", admin_user),
            ("ORACLE_ADMIN_PASSWORD", admin_pass),
            ("ORACLE_TELEGRAM_TOKEN", tg_token),
            ("ORACLE_TELEGRAM_CHAT_ID", tg_chat),
            ("ORACLE_WEBHOOK_PASSPHRASE", webhook_pass),
            ("ORACLE_WEBHOOK_HOST", wh_host),
            ("ORACLE_WEBHOOK_PORT", wh_port),
            ("ORACLE_SITE_BASE", site_base),
            ("ORACLE_CONTROL_PASSPHRASE", control_pass),
            ("ORACLE_CONTROL_POLL", "true"),
            ("ORACLE_ROUTE_EXTERNAL", "true"),
            ("ORACLE_DATA_RELAY_TOKEN", relay_token),
        ], force)

        _write_env(panel_env, [
            ("PANEL_DOMAIN", panel_domain),
            ("ORACLE_CONTROL_PASSPHRASE", control_pass),
            ("ORACLE_DATA_RELAY_TOKEN", relay_token),
            ("PANEL_PASSWORD", panel_pass),
        ], force)

        print("\\n--- Save these secrets somewhere safe ---")
        print("  webhook passphrase :", webhook_pass)
        print("  control passphrase :", control_pass)
        print("  data relay token   :", relay_token)
        print("  panel password     :", panel_pass)
        print("The control passphrase + relay token are identical in both .env")
        print("files, which is required for the bot and panel to talk.")


    def main():
        ap = argparse.ArgumentParser(description="Recreate the Oracle project and configure it.")
        ap.add_argument("--dest", default=".", help="Target directory (default: current).")
        ap.add_argument("--force", action="store_true", help="Overwrite files that already exist.")
        ap.add_argument("--configure", action="store_true", help="Only run the guided .env setup.")
        ap.add_argument("--no-setup", action="store_true", help="Write files but skip the .env wizard.")
        args = ap.parse_args()

        if args.configure:
            run_guided_setup(args.dest, args.force)
            return

        written, skipped = 0, 0
        for relpath, b64 in sorted(FILES.items()):
            target = os.path.join(args.dest, relpath)
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            if os.path.exists(target) and not args.force:
                print("  skip (exists):", relpath)
                skipped += 1
                continue
            with open(target, "wb") as fh:
                fh.write(base64.b64decode(b64))
            print("  write        :", relpath)
            written += 1

        print(f"\\nDone: {written} written, {skipped} skipped, into {os.path.abspath(args.dest)}")
        if skipped and not args.force:
            print("Re-run with --force to overwrite the skipped files.")

        if args.no_setup:
            print("\\nSkipped guided setup (--no-setup). Create the .env files per the READMEs.")
        elif sys.stdin.isatty():
            try:
                ans = input("\\nRun guided setup to create your .env files now? [Y/n]: ").strip().lower()
            except EOFError:
                ans = "n"
            if ans in ("", "y", "yes"):
                run_guided_setup(args.dest, args.force)
            else:
                print("Skipped. Run later with: python build_oracle_project.py --configure")
        else:
            run_guided_setup(args.dest, args.force)

        print("\\nNext steps:")
        print("  * oracle bot : cd oracle_citadel && pip install -r requirements.txt && python oracle_citadel.py")
        print("  * panel      : cd control_panel  && docker compose up -d --build")


    if __name__ == "__main__":
        main()
''')

with open("build_oracle_project.py", "w") as out:
    out.write(header)
    out.write(doc)
    out.write("\nFILES = {\n")
    for f in sorted(blob):
        out.write(f"    {f!r}: (\n")
        s = blob[f]
        for i in range(0, len(s), 76):
            out.write(f"        {s[i:i+76]!r}\n")
        out.write("    ),\n")
    out.write("}\n")
    out.write(body)

print("regenerated build_oracle_project.py with", len(blob), "files + guided installer")
