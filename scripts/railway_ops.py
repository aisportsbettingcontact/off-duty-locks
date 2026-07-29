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

API = "https://backboard.railway.com/graphql/v2"
TIMEOUT = 30


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #

class GraphQLError(RuntimeError):
    pass


def _headers() -> tuple[dict[str, str], str]:
    account = os.environ.get("RAILWAY_API_TOKEN", "").strip()
    project = os.environ.get("RAILWAY_TOKEN", "").strip()
    if account:
        return ({"Authorization": f"Bearer {account}",
                 "Content-Type": "application/json"}, "account/team token")
    if project:
        return ({"Project-Access-Token": project,
                 "Content-Type": "application/json"}, "project token")
    raise SystemExit(
        "NO CREDENTIALS: set RAILWAY_API_TOKEN (account/team) or RAILWAY_TOKEN "
        "(project) as a repository secret. Railway -> Account Settings -> Tokens."
    )


def gql(query: str, variables: dict | None = None) -> dict:
    headers, _ = _headers()
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(API, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise GraphQLError(f"HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise GraphQLError(f"network: {exc.reason}") from None
    if payload.get("errors"):
        msgs = "; ".join(e.get("message", "?") for e in payload["errors"])
        raise GraphQLError(msgs)
    return payload.get("data") or {}


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
    customDomains { id domain status targetPort }
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
    _, kind = _headers()
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
        print("  ! no projects visible to this token")
        print("    A PROJECT token only sees its own project and cannot list")
        print("    projects; use an account/team token for a full survey.")
        return {}

    found: dict = {}
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
                          f"-> targetPort {cd.get('targetPort')}  status {cd.get('status')}")
                    if (cd.get("domain") or "").lower() == domain.lower():
                        found = {
                            "projectId": proj["id"], "projectName": proj.get("name"),
                            "environmentId": env["id"], "environmentName": env.get("name"),
                            "serviceId": svc["id"], "serviceName": svc.get("name"),
                            "customDomainId": cd.get("id"),
                            "targetPort": cd.get("targetPort"),
                            "latestStatus": (latest or {}).get("status"),
                            "deploymentCount": len(deployments),
                        }

    print("\n" + "=" * 72)
    print(" FINDING")
    print("=" * 72)
    if not found:
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
        print("  no misconfiguration detected in port or deployment status;")
        print("  if the domain still 502s the instance is failing at runtime —")
        print("  read the deploy log for the 'Listening at:' line.")
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
