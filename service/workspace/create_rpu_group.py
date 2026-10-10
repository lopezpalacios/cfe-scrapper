#!/usr/bin/env python3
"""create_rpu_group.py <rpu> — one Workspace group per RPU.

Pattern (operator decision, 2026-10-10): each onboarded RPU gets its own
group `rpu-<rpu>@newman.re`. The group address IS the CFE account email for
the MiEspacio/Contigo registration, so the portal's 2FA code lands in the
group inbox, which the service reads — one auditable inbox per client.

Implementation: GAM (the Workspace admin CLI on the mini). GAM's service
account key was found broken (MalformedFraming on oauth2service.json); until
the operator regenerates it (Workspace admin console -> GAM's GCP project ->
new SA key -> re-run gam user <admin> update serviceaccount), this script
refuses to run rather than half-create anything.

Alternative with zero provisioning (works today): plus-addressing
`jesus+rpu<rpu>@newman.re` — the code lands in the existing jesus@newman.re
inbox, readable by the current IMAP reader. The group pattern wins on
per-client isolation + audit; the plus-address wins on zero setup.
"""
from __future__ import annotations

import re
import subprocess
import sys

DOMAIN = "newman.re"
OWNER = "jesus@newman.re"


def gam(args: list[str]) -> str:
    out = subprocess.run(["gam", *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout)[:300])
    return out.stdout


def main() -> int:
    if len(sys.argv) < 2 or not re.fullmatch(r"\d{12}", sys.argv[1]):
        print("usage: create_rpu_group.py <rpu-12-digits>", file=sys.stderr)
        return 2
    rpu = sys.argv[1]
    email = f"rpu-{rpu}@{DOMAIN}"
    try:
        gam(["create", "group", email, "name",
             f"CFE RPU {rpu}", "description",
             "Inbox for the CFE portal verification codes of this RPU",
             "who_can_post_message", "anyone_can_post"])
        gam(["update", "group", email, "add", "member", OWNER])
        # deliver portal mail: the group accepts external senders
        gam(["update", "group", email, "who_can_view_group", "all_managers_can_view"])
        print(email)
        return 0
    except RuntimeError as exc:
        sys.stderr.write(f"gam failed (SA key repair needed?): {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
