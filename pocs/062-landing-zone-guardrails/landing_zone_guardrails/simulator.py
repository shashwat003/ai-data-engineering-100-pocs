"""Service control policy (SCP) evaluation over an organisation tree.

Semantics implemented (documented AWS behaviour for SCPs):
  * every node from the root down to the account needs at least one matching Allow (FullAWSAccess is an Allow *),
  * an explicit Deny in ANY policy on the path wins,
  * SCPs do not apply to the management account or to service-linked roles.
Condition support is a subset; anything outside it raises `Unsupported` instead of guessing (fail closed).
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path

FULL_ACCESS = {
    "Version": "2012-10-17",
    "Statement": [{"Sid": "FullAWSAccess", "Effect": "Allow", "Action": "*", "Resource": "*"}],
}
POSITIVE = {"StringEquals", "StringLike", "ArnLike", "ArnEquals"}
NEGATED = {"StringNotEquals", "StringNotLike", "ArnNotLike", "ArnNotEquals"}
EXACT = {"StringEquals", "StringNotEquals", "ArnEquals", "ArnNotEquals"}


class Unsupported(ValueError):
    pass


@dataclass(frozen=True)
class Request:
    principal_arn: str
    account: str
    action: str
    resource: str = "*"
    context: tuple[tuple[str, str], ...] = ()

    def ctx(self) -> dict[str, str]:
        return {"aws:PrincipalArn": self.principal_arn, **dict(self.context)}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


def _as_list(v):
    return v if isinstance(v, list) else [v]


def _glob(pattern: str, value: str) -> bool:
    return fnmatch.fnmatchcase(
        value.lower(), pattern.lower()
    )  # IAM actions are case-insensitive; also fine for ARNs here


def _condition_matches(cond: dict, ctx: dict[str, str]) -> bool:
    for op, keys in cond.items():
        for key, values in keys.items():
            if "${" in json.dumps(values):
                raise Unsupported("policy variables are not supported")
            vals = [str(x) for x in _as_list(values)]
            present = key in ctx
            if op == "Null":
                want_absent = vals[0].lower() == "true"
                if want_absent == present:
                    return False
            elif op == "Bool":
                if not present or ctx[key].lower() != vals[0].lower():
                    return False
            elif op in POSITIVE:
                if not present or not any(
                    (v == ctx[key]) if op in EXACT else _glob(v, ctx[key]) for v in vals
                ):
                    return False
            elif op in NEGATED:
                # a missing key satisfies a negated operator; a present key must match none of the values
                if present and any(
                    (v == ctx[key]) if op in EXACT else _glob(v, ctx[key]) for v in vals
                ):
                    return False
            else:
                raise Unsupported(f"condition operator {op!r}")
    return True


def statement_matches(st: dict, req: Request) -> bool:
    if "Principal" in st or "NotPrincipal" in st:
        raise Unsupported("SCPs cannot contain a Principal element")
    acts = [a for a in _as_list(st.get("Action", st.get("NotAction", [])))]
    hit = any(_glob(a, req.action) for a in acts)
    if "NotAction" in st:
        hit = not hit
    ress = _as_list(st.get("Resource", st.get("NotResource", ["*"])))
    rhit = any(_glob(r, req.resource) for r in ress)
    if "NotResource" in st:
        rhit = not rhit
    return hit and rhit and _condition_matches(st.get("Condition", {}), req.ctx())


@dataclass
class Node:
    name: str
    policies: list[str]
    children: list[Node]
    accounts: list[str]


class Organisation:
    def __init__(self, tree: Node, policies: dict[str, dict], management_account: str):
        self.tree, self.policies, self.management = (
            tree,
            {"FullAWSAccess": FULL_ACCESS, **policies},
            management_account,
        )

    @classmethod
    def load(cls, org_json: str | Path, policy_dir: str | Path) -> Organisation:
        cfg = json.loads(Path(org_json).read_text())
        pols = {p.stem: json.loads(p.read_text()) for p in Path(policy_dir).glob("*.json")}
        r = cfg["root"]
        kids = [Node(n, o["policies"], [], o["accounts"]) for n, o in r["ous"].items()]
        return cls(Node("root", r["policies"], kids, []), pols, cfg["management_account"])

    def with_attachments(self, attachments: dict[str, list[str]]) -> Organisation:
        """A copy whose policy attachments are replaced by the ACTUAL state read back from the organisation."""
        import copy

        o = copy.deepcopy(self)
        for n in [o.tree, *o.tree.children]:
            n.policies = list(attachments[n.name])
        return o

    def path_to(self, account: str) -> list[Node]:
        for ou in self.tree.children:
            if account in ou.accounts:
                return [self.tree, ou]
        if account == self.management:
            return [self.tree]
        raise KeyError(f"unknown account {account}")

    def evaluate(self, req: Request) -> Decision:
        if req.account == self.management:
            return Decision(True, "management account is not subject to SCPs")
        if "/aws-service-role/" in req.principal_arn:
            return Decision(True, "service-linked roles are not subject to SCPs")
        nodes = self.path_to(req.account)
        for n in nodes:  # explicit deny anywhere wins
            for pname in n.policies:
                for st in self.policies[pname]["Statement"]:
                    if st.get("Effect") == "Deny" and statement_matches(st, req):
                        return Decision(
                            False, f"explicit deny: {pname}/{st.get('Sid', '?')} at {n.name}"
                        )
        for n in nodes:  # every level needs an allow
            if not any(
                st.get("Effect") == "Allow" and statement_matches(st, req)
                for p in n.policies
                for st in self.policies[p]["Statement"]
            ):
                return Decision(False, f"implicit deny: no Allow at {n.name}")
        return Decision(True, "allowed at every level, no explicit deny")
