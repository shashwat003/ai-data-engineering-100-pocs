"""Child process: applies runtime restrictions then executes the untrusted code. Layers 2 and 3.

Run as `python -I _runner.py <codefile> <json-config>`. Never imported by the parent.
"""

import builtins
import json
import resource
import sys

code_path, cfg = sys.argv[1], json.loads(sys.argv[2])

if cfg["rlimits"]:  # layer 3: kernel-enforced limits, cannot be lifted by the guest code
    resource.setrlimit(resource.RLIMIT_CPU, (cfg["cpu_s"], cfg["cpu_s"]))
    resource.setrlimit(resource.RLIMIT_AS, (cfg["mem_mb"] << 20, cfg["mem_mb"] << 20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        resource.setrlimit(
            resource.RLIMIT_NPROC, (0, 0)
        )  # not enforced for root; effective for unprivileged users
    except (ValueError, OSError):
        pass

source = open(code_path).read()
real_import = builtins.__import__
ALLOWED = set(cfg["allowed_imports"])

if cfg[
    "restricted_builtins"
]:  # layer 2: the guest sees a small builtins table and a filtering import hook
    SAFE = (
        "abs all any ascii bin bool bytearray bytes callable chr complex dict divmod enumerate filter float format frozenset hash hex int "
        "isinstance issubclass iter len list map max min next object oct ord pow print range repr reversed round set slice sorted str sum tuple "
        "type zip ArithmeticError AssertionError AttributeError Exception IndexError KeyError KeyboardInterrupt LookupError MemoryError NameError "
        "OverflowError RecursionError RuntimeError StopIteration TypeError ValueError ZeroDivisionError True False None"
    ).split()
    table = {k: getattr(builtins, k) for k in SAFE if hasattr(builtins, k)}

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name.split(".")[0] not in ALLOWED:
            raise ImportError(f"import of '{name}' is blocked")
        return real_import(name, globals, locals, fromlist, level)

    table["__import__"] = guarded_import
    env = {"__builtins__": table, "__name__": "__sandbox__"}
else:
    env = {"__name__": "__sandbox__"}

sys.setrecursionlimit(cfg["recursion"])
try:
    exec(compile(source, "<guest>", "exec"), env)
except SystemExit:
    pass
except BaseException as e:  # report the error type only; tracebacks would leak host paths
    print(f"{type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
    sys.exit(3)
