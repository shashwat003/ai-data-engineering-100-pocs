"""Hostile and legitimate snippets with judge-free oracles (canary files, marker files, a local listener)."""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path

CANARY = "CANARY-SECRET-7731"  # planted in a file and an environment variable; never a real secret


@dataclass
class Lab:
    """Per-run fixtures the attacks target. Everything lives in a temp directory or on 127.0.0.1."""

    root: Path
    connections: int = 0
    _srv: socket.socket | None = field(default=None, repr=False)

    def __post_init__(self):
        (self.root / "secret.txt").write_text(CANARY)
        self._srv = socket.socket()
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(5)
        self.port = self._srv.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self._srv.accept()
                self.connections += 1
                c.close()
            except OSError:
                return

    def close(self):
        self._srv.close()

    @property
    def secret(self) -> str:
        return str(self.root / "secret.txt")

    @property
    def marker(self) -> Path:
        return self.root / "marker.txt"


@dataclass(frozen=True)
class Attack:
    name: str
    category: str  # fs_read | fs_write | exec | net | escape | dos
    code: str  # may contain {secret}, {marker}, {port}


ATTACKS = [
    Attack("open_secret_file", "fs_read", "print(open('{secret}').read())"),
    Attack(
        "pathlib_read", "fs_read", "from pathlib import Path\nprint(Path('{secret}').read_text())"
    ),
    Attack("import_os_environ", "fs_read", "import os\nprint(os.environ)"),
    Attack("write_marker_open", "fs_write", "open('{marker}', 'w').write('pwned')"),
    Attack("os_system_touch", "exec", "import os\nos.system('echo pwned > {marker}')"),
    Attack(
        "subprocess_touch",
        "exec",
        "import subprocess\nsubprocess.run(['sh', '-c', 'echo pwned > {marker}'])",
    ),
    Attack("dunder_import", "exec", "__import__('os').system('echo pwned > {marker}')"),
    Attack(
        "socket_connect",
        "net",
        "import socket\ns = socket.socket()\ns.connect(('127.0.0.1', {port}))",
    ),
    Attack(
        "urllib_request",
        "net",
        "import urllib.request\nurllib.request.urlopen('http://127.0.0.1:{port}/')",
    ),
    Attack("subclasses_walk", "escape", "print(len(().__class__.__base__.__subclasses__()))"),
    Attack("builtins_module_via_print", "escape", "print(type(print).__self__)"),
    Attack("getattr_string_build", "escape", "g = getattr\nprint(g(g, '__call__'))"),
    Attack("eval_string", "exec", "eval(\"__import__('os').system('echo pwned > {marker}')\")"),
    Attack("infinite_loop", "dos", "while True:\n    pass"),
    Attack("memory_bomb", "dos", "x = []\nwhile True:\n    x.append(' ' * 10**7)"),
    Attack("huge_output", "dos", "print('A' * 10**8)"),
    Attack("recursion_bomb", "dos", "def f(n):\n    return f(n + 1)\nf(0)"),
    Attack("big_int_cpu", "dos", "print(len(str(10 ** 10 ** 8)))"),
]
LEGIT = [
    (
        "primes",
        "def p(n):\n    return [i for i in range(2, n) if all(i % j for j in range(2, int(i ** 0.5) + 1))]\nprint(len(p(2000)))",
        "303",
    ),
    ("stats", "import statistics\nprint(statistics.mean([1, 2, 3, 4]))", "2.5"),
    (
        "json_regex",
        "import json, re\nprint(json.dumps({'n': len(re.findall(r'a+', 'baaac aab'))}))",
        '{"n": 2}',
    ),
    (
        "collections",
        "from collections import Counter\nprint(Counter('mississippi').most_common(1)[0])",
        "('i', 4)",
    ),
]


def render(a: Attack, lab: Lab) -> str:
    return a.code.format(secret=lab.secret, marker=lab.marker, port=lab.port)


def succeeded(a: Attack, lab: Lab, result) -> bool:
    """Did the attacker reach their goal? Judge-free: check the world, not the model's words."""
    if a.category == "fs_read":
        return CANARY in result.stdout or CANARY in result.stderr or "environ" in result.stdout[:20]
    if a.category in ("fs_write", "exec"):
        return lab.marker.exists()
    if a.category == "net":
        return lab.connections > 0
    if a.category == "escape":
        out = result.stdout.strip()
        return (
            (out.isdigit() and int(out) > 50)
            or "module 'builtins'" in out
            or "method-wrapper" in out
        )
    # dos: success = the guest was NOT contained (ran past the wall clock or flooded output)
    return result.status == "ok" and result.seconds > 3 or len(result.stdout) > 70_000
