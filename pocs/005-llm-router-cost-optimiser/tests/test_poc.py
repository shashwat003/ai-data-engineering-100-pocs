import dataclasses
import json
from pathlib import Path

import pytest
from llm_router_cost_optimiser.evaluate import (
    paired_accuracy_diff,
    pick_operating_point,
    render_markdown,
    run_experiment,
    run_policy,
    threshold_sweep,
)
from llm_router_cost_optimiser.main import main
from llm_router_cost_optimiser.models import (
    LLM_PROFILE,
    SLM_PROFILE,
    CachedModel,
    OllamaModel,
    SimulatedModel,
    parse_reply,
)
from llm_router_cost_optimiser.router import (
    CascadeRouter,
    FeatureRouter,
    OracleRouter,
    SingleModelRouter,
)
from llm_router_cost_optimiser.tasks import (
    generate_tasks,
    heuristic_difficulty,
    is_correct,
)

from llm_client import StubClient
from pocs_common import CostLedger, load_prices

PRICES = load_prices(Path(__file__).resolve().parent.parent / "config" / "prices.json")


@pytest.fixture(scope="module")
def tasks():
    return generate_tasks(200, seed=3)


@pytest.fixture(scope="module")
def models():
    return CachedModel(SimulatedModel(SLM_PROFILE)), CachedModel(SimulatedModel(LLM_PROFILE))


def test_tasks_deterministic_and_graded(tasks):
    assert [t.prompt for t in tasks] == [t.prompt for t in generate_tasks(200, seed=3)]
    assert {t.kind for t in tasks} == {"classify", "arith"}
    assert all(is_correct(t, t.gold) for t in tasks)
    arith = next(t for t in tasks if t.kind == "arith")
    assert not is_correct(arith, "not a number") and not is_correct(arith, str(int(arith.gold) + 1))


def test_heuristic_difficulty_tracks_ground_truth(tasks):
    pairs = [(heuristic_difficulty(t.prompt), t.difficulty) for t in tasks]
    assert all(abs(h - d) <= 0.06 for h, d in pairs)  # generator jitter is +/-0.05


def test_simulated_models_are_deterministic_and_capability_ordered(tasks, models):
    small, large = models
    assert small.generate(tasks[0]) == CachedModel(SimulatedModel(SLM_PROFILE)).generate(tasks[0])
    acc = lambda m: sum(is_correct(t, m.generate(t).answer) for t in tasks) / len(tasks)  # noqa: E731
    assert acc(large) > acc(small) + 0.15
    assert SimulatedModel(SLM_PROFILE).p_correct(0.1) > SimulatedModel(SLM_PROFILE).p_correct(0.9)


def test_confidence_is_informative_when_calibrated(tasks):
    m = SimulatedModel(SLM_PROFILE)
    outs = [(is_correct(t, (o := m.generate(t)).answer), o.confidence) for t in tasks]
    right = [c for ok, c in outs if ok]
    wrong = [c for ok, c in outs if not ok]
    assert sum(right) / len(right) > sum(wrong) / len(wrong) + 0.2


def test_cascade_threshold_extremes_match_single_model_costs(tasks, models):
    small, large = models
    never = run_policy(CascadeRouter(small, large, 0.0), tasks, PRICES)
    always = run_policy(CascadeRouter(small, large, 1.01), tasks, PRICES)
    only_small = run_policy(SingleModelRouter(small, "s"), tasks, PRICES)
    only_large = run_policy(SingleModelRouter(large, "l"), tasks, PRICES)
    assert never.escalation_rate == 0 and never.cost_usd == pytest.approx(only_small.cost_usd)
    assert always.escalation_rate == 1 and always.accuracy == only_large.accuracy
    assert always.cost_usd == pytest.approx(only_small.cost_usd + only_large.cost_usd)  # waste
    assert always.mean_latency_ms > only_large.mean_latency_ms  # sequential calls add latency


def test_cascade_saves_cost_with_bounded_accuracy_loss(tasks, models):
    small, large = models
    base = run_policy(SingleModelRouter(large, "l"), tasks, PRICES)
    sweep = threshold_sweep(small, large, tasks, PRICES)
    best = pick_operating_point(sweep, base.accuracy, 0.03)
    assert best is not None and best.cost_usd < base.cost_usd
    assert best.accuracy >= base.accuracy - 0.03
    assert pick_operating_point(sweep, 2.0, 0.0) is None  # unattainable accuracy


def test_oracle_is_an_upper_bound_on_accuracy_and_cheaper_than_always_large(tasks, models):
    small, large = models
    oracle = run_policy(OracleRouter(small, large), tasks, PRICES)
    always_large = run_policy(SingleModelRouter(large, "l"), tasks, PRICES)
    assert oracle.cost_usd < always_large.cost_usd
    for r in threshold_sweep(small, large, tasks, PRICES, (0.3, 0.5, 0.7)):
        assert oracle.accuracy >= r.accuracy


def test_feature_router_sends_hard_prompts_to_large(tasks, models):
    small, large = models
    res = run_policy(FeatureRouter(small, large, 0.5), tasks, PRICES)
    hard_share = sum(heuristic_difficulty(t.prompt) >= 0.5 for t in tasks) / len(tasks)
    assert res.escalation_rate == pytest.approx(hard_share)


def test_budget_guard_degrades_instead_of_overspending(tasks, models):
    small, large = models
    free = run_policy(CascadeRouter(small, large, 0.9), tasks, PRICES)
    capped = run_policy(
        CascadeRouter(small, large, 0.9), tasks, PRICES, budget_usd=free.cost_usd / 3
    )
    assert capped.degraded_rate > 0 and capped.cost_usd < free.cost_usd
    ledger = CostLedger(PRICES, budget_usd=free.cost_usd / 3)
    CascadeRouter(small, large, 0.9).route(tasks[0], ledger)  # a single call never raises
    assert ledger.total > 0


def test_paired_diff_is_zero_for_identical_runs():
    mean, lo, hi = paired_accuracy_diff([True, False] * 20, [True, False] * 20)
    assert (mean, lo, hi) == (0.0, 0.0, 0.0)


def test_parse_reply_and_ollama_adapter(tasks):
    assert parse_reply("ANSWER: Billing. CONFIDENCE: 0.85") == ("billing", 0.85, True)
    assert parse_reply("ANSWER: 42") == ("42", 0.5, False)
    assert parse_reply("no format at all")[0] == "no format at all"
    assert not parse_reply("no format at all").well_formed
    assert parse_reply("ANSWER: x CONFIDENCE: 7")[1] == 1.0
    task = tasks[0]
    client = StubClient(lambda p: f"ANSWER: {task.gold} CONFIDENCE: 0.9")
    model = OllamaModel(client, "llama3.2:3b")
    out = model.generate(task)
    assert is_correct(task, out.answer) and out.confidence == 0.9
    assert "CONFIDENCE" in client.calls[0]


def test_full_experiment_and_report(tasks):
    res = run_experiment(tasks, PRICES, max_drop=0.03)
    names = [p["name"] for p in res["policies"]]
    assert {"always_small", "always_large", "cascade_best", "oracle"} <= set(names)
    assert res["operating_point"]["cost_saving_vs_always_large"] > 0
    assert len(res["calibration_study"]) == 5
    md = render_markdown(
        res, {"backend": "sim", "n_tasks": 200, "seed": 3, "small": "a", "large": "b"}
    )
    assert "Simulation." in md and "cascade_best" in md and "Budget guard" in md
    json.dumps(res)  # must be serialisable


def test_calibration_zero_removes_cascade_advantage(tasks):
    res = run_experiment(tasks, PRICES, max_drop=0.02)
    by_cal = {c["small_calibration"]: c for c in res["calibration_study"]}
    informative = by_cal[0.35]["cost_saving_vs_always_large"]
    uninformative = by_cal[0.0].get("cost_saving_vs_always_large", 0.0)
    assert informative > uninformative


def test_cli_writes_outputs(tmp_path):
    main(["--n-tasks", "60", "--out-dir", str(tmp_path), "--max-acc-drop", "0.05"])
    data = json.loads((tmp_path / "results.json").read_text())
    assert data["meta"]["backend"] == "sim" and data["meta"]["n_tasks"] == 60
    assert (tmp_path / "results.md").read_text().startswith("# Results")
    assert dataclasses.is_dataclass(SLM_PROFILE)


# ---- Ollama integration, exercised against a protocol-faithful STUB server (not a real model)

import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402

from llm_router_cost_optimiser.models import normalise_answer, preflight  # noqa: E402

from llm_client import LLMError, OllamaClient  # noqa: E402


def _stub_server(tasks, installed=("tiny:1b", "big:8b")):
    by_prompt = {t.prompt: t for t in tasks}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            if self.path == "/api/version":
                self._send({"version": "0.0-stub"})
            else:
                self._send(
                    {
                        "models": [
                            {"name": n, "digest": "d" * 20, "details": {"parameter_size": "x"}}
                            for n in installed
                        ]
                    }
                )

        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            prompt = body["prompt"].replace(OllamaModel.SUFFIX, "")
            task = by_prompt.get(prompt)
            if task is None:  # warm-up call
                text = "OK"
            elif body["model"] == "big:8b" or task.difficulty < 0.4:
                text = f"ANSWER: {task.gold} CONFIDENCE: 0.95"
            else:
                wrong = (
                    "0" if task.kind == "arith" else next(c for c in task.choices if c != task.gold)
                )
                text = f"ANSWER: {wrong} CONFIDENCE: 0.2"
            self._send({"response": text, "prompt_eval_count": 50, "eval_count": 8})

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def prices_file(tmp_path):
    f = tmp_path / "prices.json"
    f.write_text(
        json.dumps(
            {
                "tiny:1b": {"input_per_mtok": 0.05, "output_per_mtok": 0.1},
                "big:8b": {"input_per_mtok": 3.0, "output_per_mtok": 15.0},
            }
        )
    )
    return f


def test_ollama_backend_end_to_end_against_stub_server(tmp_path, prices_file, monkeypatch):
    tasks = generate_tasks(40, seed=11)
    server = _stub_server(tasks)
    try:
        monkeypatch.setenv("OLLAMA_URL", f"http://127.0.0.1:{server.server_port}")
        main(
            [
                "--backend",
                "ollama",
                "--n-tasks",
                "40",
                "--small-model",
                "tiny:1b",
                "--large-model",
                "big:8b",
                "--prices",
                str(prices_file),
                "--out-dir",
                str(tmp_path / "out"),
            ]
        )
    finally:
        server.shutdown()
    out = tmp_path / "out"
    assert (out / "results_ollama.md").exists() and not (out / "results.md").exists()
    data = json.loads((out / "results_ollama.json").read_text())
    assert data["meta"]["ollama"]["ollama_version"] == "0.0-stub"
    assert set(data["meta"]["ollama"]["models"]) == {"tiny:1b", "big:8b"}
    assert data["meta"]["malformed_rate"] == {"tiny:1b": 0.0, "big:8b": 0.0}
    pol = {p["name"]: p for p in data["policies"]}
    assert pol["always_large"]["accuracy"] == 1.0 and pol["always_small"]["accuracy"] < 1.0
    assert pol["cascade_best"]["accuracy"] == 1.0  # the stub's confidence separates perfectly
    assert pol["cascade_best"]["cost_usd"] < pol["always_large"]["cost_usd"]
    assert data["calibration_study"] == []  # simulation-only analysis is skipped for real models
    md = (out / "results_ollama.md").read_text()
    assert "Measured on real local models" in md and "hypothetical" in md
    assert "stub" not in md.lower().replace("0.0-stub", "")


def test_ollama_backend_fails_fast_with_actionable_messages(tmp_path, prices_file, monkeypatch):
    base = [
        "--backend",
        "ollama",
        "--n-tasks",
        "5",
        "--small-model",
        "tiny:1b",
        "--large-model",
        "big:8b",
        "--prices",
        str(prices_file),
        "--out-dir",
        str(tmp_path),
    ]
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:1")
    with pytest.raises(SystemExit, match="cannot reach Ollama"):
        main(base)
    server = _stub_server([], installed=("tiny:1b",))
    try:
        monkeypatch.setenv("OLLAMA_URL", f"http://127.0.0.1:{server.server_port}")
        with pytest.raises(SystemExit, match="ollama pull big:8b"):
            main(base)
    finally:
        server.shutdown()
    with pytest.raises(SystemExit, match="must differ"):
        main(["--backend", "ollama", "--small-model", "tiny:1b", "--large-model", "tiny:1b"])
    with pytest.raises(SystemExit, match="hypothetical prices"):
        main(
            [
                "--backend",
                "ollama",
                "--small-model",
                "nope:1b",
                "--large-model",
                "big:8b",
                "--prices",
                str(prices_file),
            ]
        )
    assert list(tmp_path.iterdir()) == [prices_file]  # nothing written on failure


def test_preflight_warms_up_each_model_once():
    server = _stub_server([])
    try:
        client = OllamaClient(f"http://127.0.0.1:{server.server_port}", timeout=5)
        info = preflight(client, ["tiny:1b", "big:8b", "tiny:1b"])
        assert list(info["models"]) == ["tiny:1b", "big:8b"]
        with pytest.raises(LLMError, match="not installed"):
            preflight(client, ["missing:1b"])
    finally:
        server.shutdown()


def test_normalise_answer_extracts_label_and_integer(tasks):
    arith = next(t for t in tasks if t.kind == "arith")
    cls = next(t for t in tasks if t.kind == "classify")
    assert normalise_answer(arith, "the result is 1,234 apples") == "1234"
    assert normalise_answer(arith, "unknown") == "unknown"
    assert normalise_answer(cls, "I think it is outage, maybe billing") == "outage"
    assert normalise_answer(cls, "gibberish") == "gibberish"
