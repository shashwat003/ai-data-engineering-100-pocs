import json

import pytest
from step_functions_ml_pipeline.asl import (
    AslError,
    Machine,
    StateError,
    build_params,
    get_path,
    set_path,
    validate,
)
from step_functions_ml_pipeline.main import INPUT, run
from step_functions_ml_pipeline.mock_stack import deploy_and_inspect
from step_functions_ml_pipeline.pipeline import DEFINITION, Tasks


def machine(states, start="A", resources=None, **kw):
    return Machine({"StartAt": start, "States": states}, resources or {}, **kw)


# --- JSONPath subset and data flow --------------------------------------------------------------------------
def test_paths():
    doc = {"a": {"b": [10, {"c": 5}]}}
    assert get_path(doc, "$.a.b[1].c") == 5 and get_path(doc, "$") == doc
    assert (
        set_path(doc, "$.a.new", 1)["a"]["new"] == 1 and "new" not in doc["a"]
    )  # no mutation of the input
    assert set_path({}, "$.x.y", 2) == {"x": {"y": 2}} and set_path(doc, "$", 3) == 3
    for bad in ("$..a", "$.a[*]", "a.b", "$.a.b[x]"):
        with pytest.raises(StateError):
            get_path(doc, bad)
    with pytest.raises(StateError):
        get_path(doc, "$.nope")


def test_parameters_and_result_path_output_path():
    assert build_params({"x.$": "$.a", "k": [{"y.$": "$.b"}], "lit": 1}, {"a": 1, "b": 2}) == {
        "x": 1,
        "k": [{"y": 2}],
        "lit": 1,
    }
    m = machine(
        {
            "A": {
                "Type": "Task",
                "Resource": "f",
                "Parameters": {"v.$": "$.n"},
                "ResultPath": "$.r",
                "OutputPath": "$.r",
                "End": True,
            }
        },
        resources={"f": lambda p: p["v"] * 2},
    )
    assert m.start({"n": 4}).output == 8
    keep = machine(
        {"A": {"Type": "Task", "Resource": "f", "ResultPath": None, "End": True}},
        resources={"f": lambda p: "ignored"},
    )
    assert keep.start({"n": 1}).output == {"n": 1}  # ResultPath null discards the result


# --- choice ----------------------------------------------------------------------------------------------------
CH = {"A": {"Type": "Choice", "Choices": [{"And": [{"Variable": "$.n", "NumericGreaterThan": 5}, {"Not": {"Variable": "$.s", "StringEquals": "x"}}], "Next": "Hi"}, {"Variable": "$.opt", "IsPresent": True, "Next": "Opt"}], "Default": "Lo"},
      "Hi": {"Type": "Pass", "Result": "hi", "End": True}, "Opt": {"Type": "Pass", "Result": "opt", "End": True}, "Lo": {"Type": "Pass", "Result": "lo", "End": True}}  # fmt: skip


@pytest.mark.parametrize(
    "inp,out",
    [
        ({"n": 9, "s": "y"}, "hi"),
        ({"n": 9, "s": "x"}, "lo"),
        ({"n": 1, "s": "y", "opt": 1}, "opt"),
        ({"n": "9", "s": "y"}, "lo"),
        ({"s": "y"}, "lo"),
    ],
)
def test_choice_rules(inp, out):
    assert (
        machine(CH).start(inp).output == out
    )  # a string where a number is expected never matches a numeric rule


def test_choice_without_default_fails_at_runtime_and_is_flagged_by_validate():
    defn = {
        "StartAt": "A",
        "States": {
            "A": {
                "Type": "Choice",
                "Choices": [{"Variable": "$.n", "NumericEquals": 1, "Next": "B"}],
            },
            "B": {"Type": "Succeed"},
        },
    }
    assert any("Default" in e for e in validate(defn))


# --- retry / catch ---------------------------------------------------------------------------------------------
def flaky(n_fail, exc=ValueError):
    c = {"n": 0}

    def f(_):
        c["n"] += 1
        if c["n"] <= n_fail:
            raise exc("boom")
        return "ok"

    return f, c


def test_retry_with_exponential_backoff_then_success():
    f, c = flaky(2)
    slept = []
    m = machine(
        {
            "A": {
                "Type": "Task",
                "Resource": "f",
                "Retry": [
                    {
                        "ErrorEquals": ["ValueError"],
                        "IntervalSeconds": 2,
                        "MaxAttempts": 3,
                        "BackoffRate": 3.0,
                    }
                ],
                "End": True,
            }
        },
        resources={"f": f},
        sleep=slept.append,
    )
    ex = m.start({})
    assert ex.status == "SUCCEEDED" and c["n"] == 3 and slept == [2, 6] and ex.slept == 8


def test_retry_exhausted_then_catch_routes_with_error_in_result_path():
    f, c = flaky(99)
    m = machine({"A": {"Type": "Task", "Resource": "f", "Retry": [{"ErrorEquals": ["ValueError"], "MaxAttempts": 2, "IntervalSeconds": 1}], "Catch": [{"ErrorEquals": ["States.ALL"], "Next": "B", "ResultPath": "$.err"}], "Next": "C"},
                 "B": {"Type": "Pass", "End": True}, "C": {"Type": "Succeed"}}, resources={"f": f})  # fmt: skip
    ex = m.start({"keep": 1})
    assert c["n"] == 3 and ex.output == {"keep": 1, "err": {"Error": "ValueError", "Cause": "boom"}}


def test_unmatched_error_fails_the_execution():
    f, _ = flaky(99, KeyError)
    ex = machine(
        {
            "A": {
                "Type": "Task",
                "Resource": "f",
                "Retry": [{"ErrorEquals": ["ValueError"]}],
                "End": True,
            }
        },
        resources={"f": f},
    ).start({})
    assert ex.status == "FAILED" and ex.error == "KeyError"


def test_timeout_maps_to_states_timeout_and_task_failed_excludes_it():
    def slow(_):
        raise TimeoutError("too slow")

    st = {
        "Type": "Task",
        "Resource": "f",
        "Retry": [{"ErrorEquals": ["States.TaskFailed"], "MaxAttempts": 5}],
        "End": True,
    }
    ex = machine({"A": st}, resources={"f": slow}).start({})
    assert ex.error == "States.Timeout" and not any(h[1].startswith("Retry") for h in ex.history)


def test_states_runtime_errors_are_never_caught():
    ex = machine(
        {
            "A": {
                "Type": "Pass",
                "Parameters": {"x.$": "$.missing"},
                "Catch": [{"ErrorEquals": ["States.ALL"], "Next": "B"}],
                "Next": "B",
            },
            "B": {"Type": "Succeed"},
        }
    ).start({})
    assert ex.status == "FAILED" and ex.error == "States.Runtime"


def test_fail_state_and_loop_guard():
    ex = machine({"A": {"Type": "Fail", "Error": "Custom", "Cause": "why"}}).start({})
    assert (ex.status, ex.error, ex.cause) == ("FAILED", "Custom", "why")
    loop = machine(
        {
            "A": {"Type": "Pass", "Next": "B"},
            "B": {
                "Type": "Choice",
                "Choices": [{"Variable": "$.stop", "BooleanEquals": True, "Next": "Done"}],
                "Default": "A",
            },
            "Done": {"Type": "Succeed"},
        }
    ).start({"stop": False})
    assert loop.status == "FAILED" and "step limit" in loop.cause


# --- map / parallel / wait ---------------------------------------------------------------------------------
def test_map_and_parallel_and_wait():
    mp = machine({"A": {"Type": "Map", "ItemsPath": "$.xs", "ItemProcessor": {"StartAt": "S", "States": {"S": {"Type": "Task", "Resource": "sq", "End": True}}}, "ResultPath": "$.ys", "Next": "W"}, "W": {"Type": "Wait", "Seconds": 7, "Next": "P"},
                  "P": {"Type": "Parallel", "Branches": [{"StartAt": "X", "States": {"X": {"Type": "Pass", "Result": 1, "End": True}}}, {"StartAt": "Y", "States": {"Y": {"Type": "Pass", "Result": 2, "End": True}}}], "ResultPath": "$.par", "End": True}}, resources={"sq": lambda x: x * x})  # fmt: skip
    ex = mp.start({"xs": [1, 2, 3]})
    assert ex.output == {"xs": [1, 2, 3], "ys": [1, 4, 9], "par": [1, 2]} and ex.slept == 7
    bad = machine(
        {
            "A": {
                "Type": "Map",
                "ItemsPath": "$.xs",
                "ItemProcessor": {"StartAt": "S", "States": {"S": {"Type": "Pass", "End": True}}},
                "End": True,
            }
        }
    )
    assert bad.start({"xs": "notalist"}).error == "States.Runtime"


# --- validation ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "states,needle",
    [
        ({"A": {"Type": "Pass", "Next": "Z"}}, "not a state"),
        ({"A": {"Type": "Pass"}}, "needs Next or End"),
        ({"A": {"Type": "Pass", "End": True}, "B": {"Type": "Pass", "End": True}}, "unreachable"),
        ({"A": {"Type": "Task", "Resource": "f", "End": True, "Next": "A"}}, "both Next and End"),
        ({"A": {"Type": "Wat", "End": True}}, "unknown Type"),
        ({"A": {"Type": "Succeed", "End": True}}, "terminal"),
        (
            {"A": {"Type": "Pass", "Parameters": {"x": "States.Format('a')"}, "End": True}},
            "intrinsic",
        ),
        ({"A": {"Type": "Task", "Resource": "x.waitForTaskToken", "End": True}}, "callback"),
        (
            {
                "A": {
                    "Type": "Task",
                    "Resource": "f",
                    "Retry": [{"ErrorEquals": ["X"], "BackoffRate": 0.5}],
                    "End": True,
                }
            },
            "Retry",
        ),
        ({"A": {"Type": "Pass", "Next": "A"}}, "no terminal"),
    ],
)
def test_validate_catches_each_defect(states, needle):
    assert any(needle in e for e in validate({"StartAt": "A", "States": states}))


def test_machine_refuses_unregistered_resource():
    with pytest.raises(AslError, match="not registered"):
        machine({"A": {"Type": "Task", "Resource": "ghost", "End": True}})


# --- the ML pipeline --------------------------------------------------------------------------------------------
def test_pipeline_definition_is_valid_and_happy_path_registers_best_model(tmp_path):
    tasks = Tasks(tmp_path / "reg")
    assert validate(DEFINITION, set(tasks.resources())) == []
    ex = Machine(DEFINITION, tasks.resources()).start(INPUT)
    assert ex.status == "SUCCEEDED"
    saved = json.loads((tmp_path / "reg" / "latest.json").read_text())
    assert saved["c"] in INPUT["grid"] and saved["auc"] >= 0.8
    assert ex.output["best"]["auc"] == max(c["auc"] for c in ex.output["candidates"])


def test_pipeline_failure_paths(tmp_path):
    t = Tasks(tmp_path / "r1", storage_failures=10)
    ex = Machine(DEFINITION, t.resources()).start(INPUT)
    assert (
        ex.status == "FAILED"
        and ex.error == "PipelineFailed"
        and len(t.notifications) == 1
        and not (tmp_path / "r1" / "latest.json").exists()
    )
    t = Tasks(tmp_path / "r2")
    assert (
        Machine(DEFINITION, t.resources()).start({**INPUT, "n_rows": 150}).error
        == "DataQualityError"
    )
    t = Tasks(tmp_path / "r3")
    ex = Machine(DEFINITION, t.resources()).start({**INPUT, "label_noise": 0.45})
    assert ex.error == "PipelineFailed" and t.notifications[0]["reason"] == "metric below threshold"


def test_pipeline_retries_only_the_failing_map_item(tmp_path):
    t = Tasks(tmp_path / "r", timeouts_for_c={0.1: 1})
    ex = Machine(DEFINITION, t.resources()).start(INPUT)
    assert ex.status == "SUCCEEDED" and sum(h[1].startswith("Retry") for h in ex.history) == 1


def test_mock_deploy_roundtrip(aws):
    r = deploy_and_inspect(aws)
    assert (
        r["stored_definition_matches"]
        and r["update_roundtrip"]
        and r["listed"] == ["ml-training-pipeline"]
    )


def test_mock_deploy_refuses_invalid_definition(aws):
    with pytest.raises(ValueError, match="invalid"):
        deploy_and_inspect(aws, {"StartAt": "X", "States": {"A": {"Type": "Pass", "End": True}}})


def test_demo_end_to_end():
    r = run()
    assert r["validation_errors"] == [] and [s["status"] for s in r["scenarios"]] == [
        "SUCCEEDED",
        "SUCCEEDED",
        "FAILED",
        "SUCCEEDED",
        "FAILED",
        "FAILED",
    ]
    assert r["mock_stack"]["stored_definition_matches"]
