"""Benchmark corpus generated at run time, so no secret-shaped literal is stored in the repository."""

from __future__ import annotations

import base64
import random
import string

A = string.ascii_letters + string.digits


def rnd(rng, n, alphabet=A):
    return "".join(rng.choice(alphabet) for _ in range(n))


def positives(rng: random.Random) -> list[tuple[str, str, str]]:
    """(type, filename, content). The secret is embedded in realistic surroundings."""
    pem = "-----BEGIN " + "RSA PRIVATE KEY-----"
    gh = "gh" + "p_" + rnd(rng, 36)
    aws = (
        "AK" + "IA" + rnd(rng, 16, string.ascii_uppercase + "234567")
    )  # real key ids use the base32 alphabet
    slack = (
        "xo" + "xb-" + "-".join(rnd(rng, n, string.digits) for n in (12, 12)) + "-" + rnd(rng, 24)
    )
    stripe = "sk_" + "live_" + rnd(rng, 24)
    openai = "sk-" + rnd(rng, 40)
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(b'{"sub":"1234567890","name":"Test"}').decode().rstrip("=")
    jwt = f"{header}.{payload}.{rnd(rng, 43, A + '-_')}"
    dburl = f"postgres://app:{rnd(rng, 14)}@db.internal.example:5432/prod"
    generic = rnd(rng, 32, A + "/+")
    return [
        ("aws-access-key", "config.py", f'AWS_ACCESS_KEY_ID = "{aws}"\n'),
        ("aws-access-key", ".env", f"AWS_KEY={aws}\n"),
        (
            "github-token",
            "deploy.sh",
            f"curl -H 'Authorization: token {gh}' https://api.example/x\n",
        ),
        ("github-token", "ci.yml", f"env:\n  GH_TOKEN: {gh}\n"),
        ("slack-token", "notify.py", f'SLACK = "{slack}"\n'),
        ("stripe-key", "billing.js", f"const stripe = require('stripe')('{stripe}');\n"),
        ("openai-style-key", "llm.py", f'client = Client(api_key="{openai}")\n'),
        ("private-key-block", "id_rsa", f"{pem}\n{rnd(rng, 64)}\n-----END RSA PRIVATE KEY-----\n"),
        ("jwt", "test_auth.py", f'TOKEN = "{jwt}"\n'),
        ("db-url-password", "settings.toml", f'database_url = "{dburl}"\n'),
        ("generic-high-entropy", "app.ini", f"[auth]\nclient_secret = {generic}\n"),
        ("generic-high-entropy", "config.json", f'{{"api_key": "{generic}"}}\n'),
        ("generic-high-entropy", "run.sh", f"export SERVICE_TOKEN='{generic}'\n"),
    ]


def negatives(rng: random.Random) -> list[tuple[str, str]]:
    """Look-alikes that must NOT be reported."""
    sha = "".join(rng.choice("0123456789abcdef") for _ in range(64))
    uid = "-".join(
        "".join(rng.choice("0123456789abcdef") for _ in range(n)) for n in (8, 4, 4, 4, 12)
    )
    b64 = base64.b64encode(bytes(rng.randrange(256) for _ in range(90))).decode()
    return [
        ("hash.txt", f"checksum = {sha}\n"),
        ("ids.py", f"REQUEST_ID = '{uid}'\n"),
        ("img.html", f'<img src="data:image/png;base64,{b64}">\n'),
        ("docs.md", "Set `API_KEY` to your key; see the secrets section.\n"),
        ("aws_doc.md", "Example from the AWS docs: AKIAIOSFODNN7" + "EXAMPLE\n"),
        ("conf.py", "password = '" + "-".join(["changeme"] * 3) + "'\n"),
        ("tpl.yml", "token: ${GITHUB_TOKEN}\nsecret_key: <your-secret-key-here>\n"),
        ("code.py", "def get_token(user):\n    return cache.lookup(user)\n"),
        ("long.txt", "x = " + "a" * 40 + "\n"),
        ("allow.py", "KEY = '" + rnd(rng, 32) + "'  # pragma: allowlist secret gitleaks:allow\n"),
        ("conf.ini", "auth_mode = ldap\ntoken_ttl = 3600\n"),
        (
            "lorem.txt",
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit sed do eiusmod tempor.\n",
        ),
    ]


def holdout(rng: random.Random) -> list[tuple[str, str, str]]:
    """Formats NOT used while tuning the kit scanner (the main corpus was). Expected to expose its gaps."""
    b64 = lambda n: base64.b64encode(bytes(rng.randrange(256) for _ in range(n))).decode()  # noqa: E731
    gh = "gh" + "p_" + rnd(rng, 36)
    return [
        ("aws-secret-key", "creds.ini", f"aws_secret_access_key = {rnd(rng, 40, A + '/+')}\n"),
        (
            "k8s-secret",
            "secret.yaml",
            f"apiVersion: v1\nkind: Secret\ndata:\n  password: {b64(24)}\n",
        ),
        (
            "url-query-token",
            "client.py",
            f'URL = "https://api.example/v1/items?access_token={rnd(rng, 32)}"\n',
        ),
        (
            "azure-connection-string",
            "app.cfg",
            f"DefaultEndpointsProtocol=https;AccountName=store;AccountKey={b64(64)};EndpointSuffix=core.windows.net\n",
        ),
        ("split-token", "split.py", f'a = "{gh[:20]}"\nb = "{gh[20:]}"\ntoken = a + b\n'),
        (
            "bearer-header",
            "call.sh",
            f'curl -H "Authorization: Bearer {rnd(rng, 40)}" https://api.example/\n',
        ),
        (
            "private-key-body",
            "key.txt",
            rnd(rng, 64, A + "+/") + "\n" + rnd(rng, 64, A + "+/") + "\n",
        ),
    ]
