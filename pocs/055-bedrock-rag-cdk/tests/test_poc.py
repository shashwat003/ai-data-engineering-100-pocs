import copy
import importlib
import json
import types

import boto3
import pytest
from aws_cdk import assertions
from bedrock_rag_cdk import stack as stack_mod
from bedrock_rag_cdk.checks import check_template, monthly_cost_estimate
from bedrock_rag_cdk.local_kb import (
    AwsKnowledgeBase,
    LocalKnowledgeBase,
    chunk_tokens,
    settings_from_template,
)
from bedrock_rag_cdk.main import lint
from bedrock_rag_cdk.stack import RagSettings, synth
from botocore.stub import Stubber
from moto import mock_aws


@pytest.fixture(scope="module")
def template():
    return synth()


def types_of(t):
    return [v["Type"] for v in t["Resources"].values()]


# ---------- the synthesised template ----------


def test_stack_contains_the_whole_data_path(template):
    ts = set(types_of(template))
    assert {"AWS::S3::Bucket", "AWS::OpenSearchServerless::Collection", "AWS::OpenSearchServerless::CollectionIndex",
            "AWS::Bedrock::KnowledgeBase", "AWS::Bedrock::DataSource", "AWS::Lambda::Function"} <= ts  # fmt: skip


def test_template_has_no_policy_findings(template):
    assert check_template(template) == []


def test_cfn_lint_reports_no_errors(template):
    assert lint(template) == []


def test_assertions_api_agrees_on_key_properties():
    from aws_cdk import App, Environment

    app = App()
    st = stack_mod.BedrockRagStack(
        app, "T", env=Environment(account="123456789012", region="us-east-1")
    )
    t = assertions.Template.from_stack(st)
    t.resource_count_is("AWS::Bedrock::KnowledgeBase", 1)
    t.has_resource_properties(
        "AWS::OpenSearchServerless::Collection",
        {"Type": "VECTORSEARCH", "StandbyReplicas": "DISABLED"},
    )
    t.has_resource_properties("AWS::Bedrock::DataSource", {"DataDeletionPolicy": "RETAIN"})
    t.has_resource_properties("AWS::S3::Bucket", {"VersioningConfiguration": {"Status": "Enabled"}})


def test_settings_round_trip_through_the_template():
    t = synth(RagSettings(max_tokens=128, overlap_percentage=10, top_k=3))
    s = settings_from_template(t)
    assert (s.max_tokens, s.overlap_percentage, s.top_k) == (128, 10, 3)
    assert s.embedding_model == "amazon.titan-embed-text-v2:0" and s.index_name == "kb-index"


def test_standby_replicas_toggle_changes_the_collection_and_the_cost_model():
    on = synth(RagSettings(standby_replicas=True))
    coll = next(
        v for v in on["Resources"].values() if v["Type"] == "AWS::OpenSearchServerless::Collection"
    )
    assert coll["Properties"]["StandbyReplicas"] == "ENABLED"
    assert (
        monthly_cost_estimate(True)["opensearch_serverless_usd"]
        == 2 * monthly_cost_estimate(False)["opensearch_serverless_usd"]
    )


def test_synthesis_is_deterministic(template):
    assert json.dumps(synth(), sort_keys=True) == json.dumps(template, sort_keys=True)


# ---------- the checks themselves catch regressions (mutation tests) ----------


def mutate(t, rtype, fn):
    t = copy.deepcopy(t)
    for v in t["Resources"].values():
        if v["Type"] == rtype:
            fn(v["Properties"])
    return t


def test_check_flags_wildcard_action(template):
    def add(p):
        p["PolicyDocument"]["Statement"].append(
            {"Effect": "Allow", "Action": "s3:*", "Resource": "arn:aws:s3:::x"}
        )

    assert any(
        "wildcard action" in f for f in check_template(mutate(template, "AWS::IAM::Policy", add))
    )


def test_check_flags_wildcard_resource(template):
    def add(p):
        p["PolicyDocument"]["Statement"].append(
            {"Effect": "Allow", "Action": "bedrock:InvokeModel", "Resource": "*"}
        )

    assert any(
        "wildcard resource" in f for f in check_template(mutate(template, "AWS::IAM::Policy", add))
    )


def test_check_flags_unblocked_public_access_and_missing_encryption(template):
    f = check_template(
        mutate(
            template,
            "AWS::S3::Bucket",
            lambda p: (p.pop("PublicAccessBlockConfiguration"), p.pop("BucketEncryption")),
        )
    )
    assert any("public access" in x for x in f) and any("not encrypted" in x for x in f)


def test_check_flags_confused_deputy_trust_policy(template):
    def strip(p):
        for st in p["AssumeRolePolicyDocument"]["Statement"]:
            st.pop("Condition", None)

    assert any(
        "confused deputy" in x for x in check_template(mutate(template, "AWS::IAM::Role", strip))
    )


def test_check_flags_missing_reserved_concurrency(template):
    assert any(
        "reserved concurrency" in x
        for x in check_template(
            mutate(
                template,
                "AWS::Lambda::Function",
                lambda p: p.pop("ReservedConcurrentExecutions", None),
            )
        )
    )


def test_check_flags_bad_chunking_range(template):
    def bad(p):
        p["VectorIngestionConfiguration"]["ChunkingConfiguration"][
            "FixedSizeChunkingConfiguration"
        ]["MaxTokens"] = 5

    assert any(
        "chunking" in x for x in check_template(mutate(template, "AWS::Bedrock::DataSource", bad))
    )


# ---------- local mirror pipeline (S3 mocked by moto) ----------

DOCS = {
    "refunds.txt": "Refunds are processed within 14 days. Contact billing for refund status. " * 3,
    "shipping.txt": "Standard shipping takes 5 business days. Express shipping takes 2 business days.",
    "warranty.txt": "The warranty covers manufacturing defects for 24 months from purchase.",
}


@pytest.fixture
def s3():
    with mock_aws():
        c = boto3.client("s3", region_name="us-east-1")
        c.create_bucket(Bucket="docs")
        for k, v in DOCS.items():
            c.put_object(Bucket="docs", Key=k, Body=v.encode())
        yield c


def test_chunking_windows_and_overlap():
    words = [f"w{i}" for i in range(10)]
    ch = chunk_tokens(" ".join(words), 4, 50)
    assert ch[0] == "w0 w1 w2 w3" and ch[1] == "w2 w3 w4 w5" and ch[-1].endswith("w9")
    assert chunk_tokens("a b", 10, 20) == ["a b"] and chunk_tokens("", 10, 20) == []
    with pytest.raises(ValueError):
        chunk_tokens("a b", 0, 10)
    with pytest.raises(ValueError):
        chunk_tokens("a b", 5, 100)


def test_ingest_is_idempotent_and_incremental(s3):
    kb = LocalKnowledgeBase()
    first = kb.ingest_from_s3(s3)
    assert first["new"] == 3 and first["embedded"] > 0
    again = kb.ingest_from_s3(s3)
    assert again["embedded"] == 0 and again["unchanged"] == 3
    s3.put_object(Bucket="docs", Key="shipping.txt", Body=b"Shipping now takes 3 days.")
    s3.delete_object(Bucket="docs", Key="warranty.txt")
    third = kb.ingest_from_s3(s3)
    assert third["updated"] == 1 and third["deleted"] == 1 and third["embedded"] == 1


def test_binary_objects_are_skipped_not_fatal(s3):
    s3.put_object(Bucket="docs", Key="blob.bin", Body=bytes([0xFF, 0xFE, 0x00, 0x80]))
    assert LocalKnowledgeBase().ingest_from_s3(s3)["new"] == 3


def test_retrieval_and_bedrock_shaped_answer_with_citation(s3):
    kb = LocalKnowledgeBase()
    kb.ingest_from_s3(s3)
    r = kb.retrieve_and_generate("How long does the warranty cover manufacturing defects?")
    assert "24 months" in r["output"]["text"]
    ref = r["citations"][0]["retrievedReferences"][0]
    assert ref["location"] == {"type": "S3", "s3Location": {"uri": "s3://docs/warranty.txt"}}


def test_unanswerable_question_yields_no_citation(s3):
    kb = LocalKnowledgeBase()
    kb.ingest_from_s3(s3)
    r = kb.retrieve_and_generate("zzzz qqqq")
    assert r["citations"] == [] and "could not find" in r["output"]["text"]


# ---------- AWS client shapes, validated by botocore against the real service model (NOT against AWS) ----------


def agent_runtime():
    return boto3.client(
        "bedrock-agent-runtime",
        region_name="us-east-1",
        aws_access_key_id="mock",
        aws_secret_access_key="mock",
    )  # noqa: S106


KB_ID, MODEL = (
    "ABCDEFGHIJ",
    "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-haiku-20240307-v1:0",
)


def test_aws_client_requests_match_the_service_model():
    c = agent_runtime()
    with Stubber(c) as st:
        st.add_response("retrieve_and_generate", {"output": {"text": "ok"}, "sessionId": "s1", "citations": []},
                        {"input": {"text": "q"}, "retrieveAndGenerateConfiguration": {"type": "KNOWLEDGE_BASE", "knowledgeBaseConfiguration": {
                            "knowledgeBaseId": KB_ID, "modelArn": MODEL, "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": 5}}}}})  # fmt: skip
        st.add_response("retrieve", {"retrievalResults": []},
                        {"knowledgeBaseId": KB_ID, "retrievalQuery": {"text": "q"}, "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": 5}}})  # fmt: skip
        aws = AwsKnowledgeBase(c, KB_ID, MODEL)
        assert aws.retrieve_and_generate("q")["output"]["text"] == "ok"
        assert aws.retrieve("q")["retrievalResults"] == []
        st.assert_no_pending_responses()


def test_aws_client_rejects_a_malformed_request():
    from botocore.exceptions import ParamValidationError

    with pytest.raises(ParamValidationError):
        AwsKnowledgeBase(agent_runtime(), KB_ID, MODEL, top_k="five").retrieve_and_generate("q")  # type: ignore[arg-type]


@pytest.fixture
def handler(template, monkeypatch):
    """Load the Lambda source embedded in the stack, with a Stubber-wrapped client."""
    fn = next(
        v
        for k, v in template["Resources"].items()
        if k.startswith("Query") and v["Type"] == "AWS::Lambda::Function"
    )
    env = fn["Properties"]["Environment"]["Variables"]
    monkeypatch.setenv("KB_ID", KB_ID)
    monkeypatch.setenv("MODEL_ARN", MODEL)
    monkeypatch.setenv("TOP_K", env["TOP_K"])
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "mock")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "mock")
    mod = types.ModuleType("lambda_index")
    exec(fn["Properties"]["Code"]["ZipFile"], mod.__dict__)  # noqa: S102 - the code under test, from our own template
    return mod


def test_embedded_lambda_handler_calls_the_service_with_valid_parameters(handler):
    with Stubber(handler.client) as st:
        st.add_response("retrieve_and_generate", {"output": {"text": "A"}, "sessionId": "s1", "citations": []},
                        {"input": {"text": "hi"}, "retrieveAndGenerateConfiguration": {"type": "KNOWLEDGE_BASE", "knowledgeBaseConfiguration": {
                            "knowledgeBaseId": KB_ID, "modelArn": MODEL, "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": 5}}}}})  # fmt: skip
        out = handler.handler({"body": json.dumps({"question": "hi"})}, None)
        assert out["statusCode"] == 200 and json.loads(out["body"])["answer"] == "A"


def test_embedded_lambda_handler_rejects_empty_questions_without_calling_aws(handler):
    with Stubber(handler.client) as st:
        out = handler.handler({"body": json.dumps({"question": "   "})}, None)
        st.assert_no_pending_responses()
    assert out["statusCode"] == 400


def test_module_reload_is_clean():
    importlib.reload(stack_mod)
