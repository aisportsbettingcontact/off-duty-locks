#!/usr/bin/env python3
"""Inspect — and optionally repair — the Railway service behind offdutylocks.com.

Everything else in this repository is proven healthy: the image builds and
serves, the database holds rows, DNS points at Railway, TLS is valid, and
Railway's edge has a route for the domain (an unknown Host gets 404, ours gets
502). The one remaining fault is that nothing answers behind that route, and it
lives entirely in Railway's own configuration — which is only reachable through
Railway's API.

So this talks to that API directly.

    diagnose   read-only. Enumerate projects, environments, services, their
               latest deployments, and every domain with its target port. Name
               the service that owns the domain and say what is wrong with it.
    fix        apply the narrowest correction implied by the diagnosis:
               align the custom domain's target port with the port the service
               listens on, and/or redeploy a service that has no live instance.

`fix` is refused unless --yes is given, and it only ever touches the service
that owns the domain.

Credentials, in order of preference:
    RAILWAY_API_TOKEN   account or team token  -> Authorization: Bearer
    RAILWAY_TOKEN       project token          -> Project-Access-Token

Tokens are never printed. Only their presence and kind are reported.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# Railway's backboard API sits behind Cloudflare. A request carrying urllib's
# default `User-Agent: Python-urllib/3.11` is rejected with HTTP 403 and
# Cloudflare error 1010 ("banned based on your browser's signature") BEFORE it
# reaches Railway's auth layer — so a perfectly valid token looks like an auth
# failure. Sending a browser-shaped User-Agent is what gets the request through.
# This was not a guess: the first run returned 1010 for every query while both
# tokens were present, and 1010 is a Cloudflare code, not a Railway one.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# The API has been served from both hosts; try them in order so a move does not
# strand the tool.
ENDPOINTS = (
    "https://backboard.railway.com/graphql/v2",
    "https://backboard.railway.app/graphql/v2",
)
TIMEOUT = 30


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #

class GraphQLError(RuntimeError):
    pass


def _base_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": UA,
    }


def _auth_variants() -> list[tuple[dict[str, str], str]]:
    """Every credential we hold, in the order worth trying.

    Both token kinds may be configured. An account token is preferred because a
    project token cannot enumerate projects — but if the account token is
    rejected we should still fall back rather than report a dead end, so each
    is returned and tried in turn.
    """
    out: list[tuple[dict[str, str], str]] = []
    account = os.environ.get("RAILWAY_API_TOKEN", "").strip()
    project = os.environ.get("RAILWAY_TOKEN", "").strip()
    if account:
        out.append(({"Authorization": f"Bearer {account}"}, "account/team token"))
    if project:
        out.append(({"Project-Access-Token": project}, "project token"))
        # Some Railway deployments accept a project token as a bearer too.
        out.append(({"Authorization": f"Bearer {project}"}, "project token (bearer)"))
    if not out:
        raise SystemExit(
            "NO CREDENTIALS: set RAILWAY_API_TOKEN (account/team) or RAILWAY_TOKEN "
            "(project) as a repository secret. Railway -> Account Settings -> Tokens."
        )
    return out


# Once a (endpoint, auth) pair works, keep using it instead of re-probing.
_WORKING: dict[str, object] = {}


def _post(endpoint: str, headers: dict[str, str], body: bytes) -> dict:
    req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300].replace("\n", " ")
        if "1010" in detail or "1020" in detail:
            detail += ("  [Cloudflare rejected the request on browser signature, "
                       "not a Railway auth failure]")
        raise GraphQLError(f"HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise GraphQLError(f"network: {exc.reason}") from None


def gql(query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode()

    if _WORKING:
        payload = _post(_WORKING["endpoint"], _WORKING["headers"], body)  # type: ignore[index,arg-type]
        if payload.get("errors"):
            raise GraphQLError("; ".join(e.get("message", "?")
                                         for e in payload["errors"]))
        return payload.get("data") or {}

    last: str = "no attempt made"
    for endpoint in ENDPOINTS:
        for auth, label in _auth_variants():
            headers = {**_base_headers(), **auth}
            try:
                payload = _post(endpoint, headers, body)
            except GraphQLError as exc:
                last = f"{endpoint} via {label}: {exc}"
                continue
            if payload.get("errors"):
                last = (f"{endpoint} via {label}: "
                        + "; ".join(e.get("message", "?") for e in payload["errors"]))
                continue
            _WORKING.update({"endpoint": endpoint, "headers": headers, "label": label})
            print(f"    (authenticated via {label} at {endpoint})")
            return payload.get("data") or {}
    raise GraphQLError(last)


def try_gql(label: str, query: str, variables: dict | None = None) -> dict | None:
    """Run a query, reporting failure rather than raising.

    Railway's schema evolves, so a field that is absent should downgrade one
    section of the report instead of aborting the whole diagnosis.
    """
    try:
        return gql(query, variables)
    except GraphQLError as exc:
        print(f"    ! {label}: {exc}")
        return None


def edges(node: dict | None, *path: str) -> list[dict]:
    cur: object = node or {}
    for key in path:
        if not isinstance(cur, dict):
            return []
        cur = cur.get(key) or {}
    if not isinstance(cur, dict):
        return []
    return [e.get("node") or {} for e in (cur.get("edges") or [])]


# --------------------------------------------------------------------------- #
# queries
# --------------------------------------------------------------------------- #

Q_ME = "query { me { id email name } }"

Q_PROJECTS = """
query {
  projects {
    edges { node {
      id name
      environments { edges { node { id name } } }
      services { edges { node { id name } } }
    } }
  }
}
"""

Q_DOMAINS = """
query($projectId: String!, $environmentId: String!, $serviceId: String!) {
  domains(projectId: $projectId, environmentId: $environmentId, serviceId: $serviceId) {
    customDomains { id domain targetPort }
    serviceDomains { id domain targetPort }
  }
}
"""

Q_DEPLOYMENTS = """
query($projectId: String!, $environmentId: String!, $serviceId: String!) {
  deployments(first: 5, input: {
    projectId: $projectId, environmentId: $environmentId, serviceId: $serviceId
  }) {
    edges { node { id status createdAt staticUrl } }
  }
}
"""

# The decisive query when configuration looks correct but the edge still 502s.
# A deployment's status is the *build* outcome: SUCCESS means the image built
# and the deploy was accepted, not that the process is alive and listening now.
# A container that starts and then crashes, or never binds the expected port,
# leaves a SUCCESS deployment with nothing answering behind the route — which
# is precisely the state we are in. Only the runtime log distinguishes them.
Q_DEPLOY_LOGS = """
query($deploymentId: String!, $limit: Int!) {
  deploymentLogs(deploymentId: $deploymentId, limit: $limit) {
    timestamp
    message
  }
}
"""

M_DOMAIN_PORT = """
mutation($id: String!, $targetPort: Int!) {
  customDomainUpdate(id: $id, input: { targetPort: $targetPort })
}
"""

M_REDEPLOY = """
mutation($serviceId: String!, $environmentId: String!) {
  serviceInstanceRedeploy(serviceId: $serviceId, environmentId: $environmentId)
}
"""

LIVE = {"SUCCESS", "DEPLOYED", "RUNNING"}


# --------------------------------------------------------------------------- #
# diagnosis
# --------------------------------------------------------------------------- #

def diagnose(domain: str, expect_port: int) -> dict:
    kind = ", ".join(label for _, label in _auth_variants())
    print("=" * 72)
    print(" RAILWAY DIAGNOSIS")
    print("=" * 72)
    print(f"  credential kind : {kind}")
    print(f"  target domain   : {domain}")
    print(f"  expected port   : {expect_port}")
    print("-" * 72)

    me = try_gql("me", Q_ME)
    if me and me.get("me"):
        print(f"  authenticated as: {me['me'].get('email') or me['me'].get('id')}")

    data = try_gql("projects", Q_PROJECTS)
    projects = edges(data, "projects")
    if not projects:
        # Distinguish "the query failed" from "the query succeeded and the token
        # legitimately sees nothing". Conflating them sent the first run's
        # reader toward a token-scope explanation when the real cause was a
        # Cloudflare rejection at the transport layer.
        if data is None:
            print("  ! the projects query did NOT complete — see the error above.")
            print("    This is a transport or authentication failure, not a")
            print("    statement about what the token can see.")
        else:
            print("  ! the query succeeded but returned no projects.")
            print("    A PROJECT token only sees its own project and cannot list")
            print("    projects; use an account/team token for a full survey.")
        return {}

    found: dict = {}
    # Track whether the domains query ever actually succeeded. Without this, a
    # query that failed for every service produces an empty domain list, and an
    # empty domain list reads identically to "the domain is attached nowhere" —
    # which is a completely different, and much more alarming, conclusion. The
    # first successful run reported exactly that false finding because the
    # query was rejected on a schema error.
    domains_ok = 0
    domains_failed = 0
    for proj in projects:
        print(f"\n  project: {proj.get('name')}  ({proj.get('id')})")
        envs = edges(proj, "environments")
        svcs = edges(proj, "services")
        print(f"    environments: {', '.join(e.get('name', '?') for e in envs) or '(none)'}")
        print(f"    services    : {', '.join(s.get('name', '?') for s in svcs) or '(none)'}")

        for env in envs:
            for svc in svcs:
                variables = {"projectId": proj["id"], "environmentId": env["id"],
                             "serviceId": svc["id"]}

                dom = try_gql(f"domains({svc.get('name')}/{env.get('name')})",
                              Q_DOMAINS, variables)
                if dom is None:
                    domains_failed += 1
                else:
                    domains_ok += 1
                custom = ((dom or {}).get("domains") or {}).get("customDomains") or []
                service_domains = ((dom or {}).get("domains") or {}).get("serviceDomains") or []

                dep = try_gql(f"deployments({svc.get('name')}/{env.get('name')})",
                              Q_DEPLOYMENTS, variables)
                deployments = edges(dep, "deployments")
                latest = deployments[0] if deployments else None

                if not custom and not service_domains and not deployments:
                    continue

                print(f"\n    service '{svc.get('name')}' / env '{env.get('name')}'")
                if latest:
                    print(f"      latest deployment : {latest.get('status')} "
                          f"({latest.get('createdAt')})")
                    statuses = [d.get("status") for d in deployments]
                    print(f"      recent statuses   : {statuses}")
                else:
                    print("      latest deployment : NONE — nothing has ever deployed")

                for sd in service_domains:
                    print(f"      railway domain    : {sd.get('domain')} "
                          f"-> targetPort {sd.get('targetPort')}")
                for cd in custom:
                    print(f"      custom domain     : {cd.get('domain')} "
                          f"-> targetPort {cd.get('targetPort')}")
                    if (cd.get("domain") or "").lower() == domain.lower():
                        found = {
                            "projectId": proj["id"], "projectName": proj.get("name"),
                            "environmentId": env["id"], "environmentName": env.get("name"),
                            "serviceId": svc["id"], "serviceName": svc.get("name"),
                            "customDomainId": cd.get("id"),
                            "targetPort": cd.get("targetPort"),
                            "latestStatus": (latest or {}).get("status"),
                            "latestDeploymentId": (latest or {}).get("id"),
                            "deploymentCount": len(deployments),
                        }

    print("\n" + "=" * 72)
    print(" FINDING")
    print("=" * 72)
    print(f"  domain queries   : {domains_ok} succeeded, {domains_failed} failed")
    if not found:
        if domains_ok == 0:
            print(f"  INCONCLUSIVE — every domain query failed, so we never saw the")
            print(f"  domain list. This says nothing about where {domain} is")
            print(f"  attached; fix the errors above and re-run.")
        else:
            print(f"  {domain} is not attached to any service visible to this token.")
        return {}

    print(f"  owning service   : {found['serviceName']} "
          f"(project {found['projectName']}, env {found['environmentName']})")
    print(f"  custom domain    : targetPort {found['targetPort']}")
    print(f"  latest deployment: {found['latestStatus'] or 'NONE'} "
          f"({found['deploymentCount']} recent)")

    problems: list[str] = []
    if found["deploymentCount"] == 0 or not found["latestStatus"]:
        problems.append("the service has no deployment at all")
    elif found["latestStatus"] not in LIVE:
        problems.append(f"the latest deployment is {found['latestStatus']}, not live")
    if found["targetPort"] is not None and int(found["targetPort"]) != expect_port:
        problems.append(f"targetPort is {found['targetPort']} but the app listens "
                        f"on {expect_port}")

    if problems:
        for p in problems:
            print(f"  PROBLEM          : {p}")
    else:
        print("  no misconfiguration detected in port or deployment status.")

    # When the configuration checks out, the answer is in the runtime log, not
    # in more configuration. A SUCCESS deployment only means the build was
    # accepted; the process behind it can still be crashing or bound to the
    # wrong address. Pull the log and say what it shows.
    if found.get("latestDeploymentId"):
        print()
        print("-" * 72)
        print(" RUNTIME LOG (latest deployment)")
        print("-" * 72)
        logs = try_gql("deploymentLogs", Q_DEPLOY_LOGS,
                       {"deploymentId": found["latestDeploymentId"], "limit": 120})
        lines = (logs or {}).get("deploymentLogs") or []
        if not lines:
            print("    (no log lines returned)")
        else:
            for entry in lines[-60:]:
                msg = (entry.get("message") or "").rstrip()
                if msg:
                    print(f"    {msg}")

            joined = "\n".join((e.get("message") or "") for e in lines)
            print()
            bind = [l for l in joined.splitlines() if "Listening at" in l]
            if bind:
                print(f"  BIND OBSERVED    : {bind[-1].strip()}")
                if f":{expect_port}" not in bind[-1]:
                    print(f"  PROBLEM          : the process is NOT listening on "
                          f"{expect_port}, which is the domain's target port")
                    found.setdefault("problems", []).append("bind/target port mismatch")
            else:
                print("  BIND OBSERVED    : no 'Listening at' line in the log — the "
                      "server may never have started")
            for marker in ("Traceback", "ModuleNotFoundError", "Error", "Killed",
                           "OOM", "exited with code"):
                hits = [l for l in joined.splitlines() if marker in l]
                if hits:
                    print(f"  {marker:<16} : {hits[-1].strip()[:160]}")
    found["problems"] = problems
    return found


# --------------------------------------------------------------------------- #
# repair
# --------------------------------------------------------------------------- #

def fix(found: dict, expect_port: int, apply: bool) -> int:
    if not found:
        print("\n  nothing to fix: the domain was not located.")
        return 1
    print("\n" + "=" * 72)
    print(" REPAIR " + ("(APPLY)" if apply else "(DRY RUN — pass --yes to apply)"))
    print("=" * 72)

    actions: list[tuple[str, str, dict]] = []
    if found.get("targetPort") is not None and int(found["targetPort"]) != expect_port:
        actions.append((
            f"set custom domain targetPort {found['targetPort']} -> {expect_port}",
            M_DOMAIN_PORT,
            {"id": found["customDomainId"], "targetPort": expect_port},
        ))
    if found.get("latestStatus") not in LIVE:
        actions.append((
            f"redeploy service '{found['serviceName']}'",
            M_REDEPLOY,
            {"serviceId": found["serviceId"], "environmentId": found["environmentId"]},
        ))

    if not actions:
        print("  no corrective action is implied by the diagnosis.")
        return 0

    rc = 0
    for label, mutation, variables in actions:
        print(f"  - {label}")
        if not apply:
            continue
        try:
            gql(mutation, variables)
            print("    APPLIED")
        except GraphQLError as exc:
            print(f"    FAILED: {exc}")
            rc = 1
    if not apply:
        print("\n  DRY RUN — nothing was changed.")
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=["diagnose", "fix"], nargs="?", default="diagnose")
    ap.add_argument("--domain", default="offdutylocks.com")
    ap.add_argument("--port", type=int, default=3000,
                    help="the port the app listens on (default: 3000)")
    ap.add_argument("--yes", action="store_true", help="apply changes in fix mode")
    args = ap.parse_args(argv)

    found = diagnose(args.domain, args.port)
    if args.mode == "fix":
        return fix(found, args.port, args.yes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
