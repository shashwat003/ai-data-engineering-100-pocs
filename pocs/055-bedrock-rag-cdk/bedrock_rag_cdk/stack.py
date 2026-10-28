"""CDK stack for a Bedrock knowledge-base RAG service (S3 -> Titan embeddings -> OpenSearch Serverless -> RetrieveAndGenerate).

The stack is only ever SYNTHESISED here (offline, no AWS account, no credentials). Nothing in this repository deploys it.
Every tunable that also drives the local mirror (chunk size, overlap, top-k, embedding model) lives in `RagSettings` so the
tests can prove the template and the local pipeline agree.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import aws_cdk as cdk
from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_opensearchserverless as aoss
from aws_cdk import aws_s3 as s3
from constructs import Construct

EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
GEN_MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"
EMBED_DIM = 1024
INDEX_NAME = "kb-index"

HANDLER = """
import json, os
import boto3

client = boto3.client("bedrock-agent-runtime")


def handler(event, context):
    question = str(json.loads(event.get("body") or "{}").get("question", ""))[:2000]
    if not question.strip():
        return {"statusCode": 400, "body": json.dumps({"error": "question required"})}
    r = client.retrieve_and_generate(
        input={"text": question},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": os.environ["KB_ID"],
                "modelArn": os.environ["MODEL_ARN"],
                "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": int(os.environ["TOP_K"])}},
            },
        },
    )
    return {"statusCode": 200, "body": json.dumps({"answer": r["output"]["text"], "citations": r.get("citations", [])})}
"""


@dataclass(frozen=True)
class RagSettings:
    max_tokens: int = 300
    overlap_percentage: int = 20
    top_k: int = 5
    collection_name: str = "rag-vectors"
    standby_replicas: bool = False  # DISABLED keeps the minimum OCU count (and cost) low for dev


class BedrockRagStack(cdk.Stack):
    def __init__(
        self, scope: Construct, id: str, *, settings: RagSettings | None = None, **kw
    ) -> None:
        super().__init__(scope, id, **kw)
        cfg = settings or RagSettings()
        acct, region = self.account, self.region

        docs = s3.Bucket(
            self, "Docs", encryption=s3.BucketEncryption.S3_MANAGED, enforce_ssl=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL, versioned=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )  # fmt: skip

        enc = aoss.CfnSecurityPolicy(self, "EncPolicy", name=f"{cfg.collection_name}-enc", type="encryption",
            policy=json.dumps({"Rules": [{"ResourceType": "collection", "Resource": [f"collection/{cfg.collection_name}"]}], "AWSOwnedKey": True}))  # fmt: skip
        net = aoss.CfnSecurityPolicy(self, "NetPolicy", name=f"{cfg.collection_name}-net", type="network",
            policy=json.dumps([{"Rules": [{"ResourceType": "collection", "Resource": [f"collection/{cfg.collection_name}"]},
                                          {"ResourceType": "dashboard", "Resource": [f"collection/{cfg.collection_name}"]}], "AllowFromPublic": True}]))  # fmt: skip
        collection = aoss.CfnCollection(self, "Collection", name=cfg.collection_name, type="VECTORSEARCH",
            standby_replicas="ENABLED" if cfg.standby_replicas else "DISABLED")  # fmt: skip
        collection.add_resource_dependency(enc)
        collection.add_resource_dependency(net)

        kb_role = iam.Role(self, "KbRole", assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com", conditions={
            "StringEquals": {"aws:SourceAccount": acct},
            "ArnLike": {"aws:SourceArn": f"arn:{self.partition}:bedrock:{region}:{acct}:knowledge-base/*"}}))  # fmt: skip
        embed_arn = f"arn:{self.partition}:bedrock:{region}::foundation-model/{EMBED_MODEL_ID}"
        kb_role.add_to_policy(
            iam.PolicyStatement(actions=["bedrock:InvokeModel"], resources=[embed_arn])
        )
        kb_role.add_to_policy(iam.PolicyStatement(actions=["s3:ListBucket"], resources=[docs.bucket_arn],
            conditions={"StringEquals": {"aws:ResourceAccount": acct}}))  # fmt: skip
        kb_role.add_to_policy(iam.PolicyStatement(actions=["s3:GetObject"], resources=[docs.arn_for_objects("*")],
            conditions={"StringEquals": {"aws:ResourceAccount": acct}}))  # fmt: skip
        kb_role.add_to_policy(
            iam.PolicyStatement(actions=["aoss:APIAccessAll"], resources=[collection.attr_arn])
        )

        access = aoss.CfnAccessPolicy(self, "AccessPolicy", name=f"{cfg.collection_name}-access", type="data",
            policy=cdk.Fn.sub(json.dumps([{"Rules": [
                {"ResourceType": "index", "Resource": [f"index/{cfg.collection_name}/*"], "Permission": ["aoss:CreateIndex", "aoss:DescribeIndex", "aoss:ReadDocument", "aoss:WriteDocument", "aoss:UpdateIndex"]},
                {"ResourceType": "collection", "Resource": [f"collection/{cfg.collection_name}"], "Permission": ["aoss:DescribeCollectionItems", "aoss:CreateCollectionItems", "aoss:UpdateCollectionItems"]}],
                "Principal": ["${KbRoleArn}"]}]), {"KbRoleArn": kb_role.role_arn}))  # fmt: skip

        schema = {"settings": {"index": {"knn": True}}, "mappings": {"properties": {
            "vector": {"type": "knn_vector", "dimension": EMBED_DIM, "method": {"name": "hnsw", "engine": "faiss", "space_type": "l2"}},
            "text": {"type": "text"}, "metadata": {"type": "text", "index": False}}}}  # fmt: skip
        index = aoss.CfnCollectionIndex(
            self,
            "VectorIndex",
            id=collection.attr_id,
            index_name=INDEX_NAME,
            index_schema=json.dumps(schema),
        )
        index.add_resource_dependency(access)
        index.add_resource_dependency(collection)

        kb = bedrock.CfnKnowledgeBase(self, "KnowledgeBase", name=f"{cfg.collection_name}-kb", role_arn=kb_role.role_arn,
            knowledge_base_configuration=bedrock.CfnKnowledgeBase.KnowledgeBaseConfigurationProperty(type="VECTOR",
                vector_knowledge_base_configuration=bedrock.CfnKnowledgeBase.VectorKnowledgeBaseConfigurationProperty(embedding_model_arn=embed_arn)),
            storage_configuration=bedrock.CfnKnowledgeBase.StorageConfigurationProperty(type="OPENSEARCH_SERVERLESS",
                opensearch_serverless_configuration=bedrock.CfnKnowledgeBase.OpenSearchServerlessConfigurationProperty(
                    collection_arn=collection.attr_arn, vector_index_name=INDEX_NAME,
                    field_mapping=bedrock.CfnKnowledgeBase.OpenSearchServerlessFieldMappingProperty(vector_field="vector", text_field="text", metadata_field="metadata"))))  # fmt: skip
        kb.add_resource_dependency(index)
        kb.node.add_dependency(kb_role)

        source = bedrock.CfnDataSource(self, "DataSource", knowledge_base_id=kb.attr_knowledge_base_id, name="docs",
            data_source_configuration=bedrock.CfnDataSource.DataSourceConfigurationProperty(type="S3",
                s3_configuration=bedrock.CfnDataSource.S3DataSourceConfigurationProperty(bucket_arn=docs.bucket_arn)),
            vector_ingestion_configuration=bedrock.CfnDataSource.VectorIngestionConfigurationProperty(
                chunking_configuration=bedrock.CfnDataSource.ChunkingConfigurationProperty(chunking_strategy="FIXED_SIZE",
                    fixed_size_chunking_configuration=bedrock.CfnDataSource.FixedSizeChunkingConfigurationProperty(
                        max_tokens=cfg.max_tokens, overlap_percentage=cfg.overlap_percentage))),
            data_deletion_policy="RETAIN")  # fmt: skip

        gen_arn = f"arn:{self.partition}:bedrock:{region}::foundation-model/{GEN_MODEL_ID}"
        fn = lambda_.Function(self, "Query", runtime=lambda_.Runtime.PYTHON_3_12, handler="index.handler",
            code=lambda_.Code.from_inline(HANDLER), timeout=cdk.Duration.seconds(30), memory_size=256,
            reserved_concurrent_executions=5, log_group=logs.LogGroup(self, "QueryLogs", retention=logs.RetentionDays.ONE_MONTH, removal_policy=cdk.RemovalPolicy.DESTROY),
            environment={"KB_ID": kb.attr_knowledge_base_id, "MODEL_ARN": gen_arn, "TOP_K": str(cfg.top_k)})  # fmt: skip
        fn.add_to_role_policy(iam.PolicyStatement(actions=["bedrock:RetrieveAndGenerate", "bedrock:Retrieve"],
            resources=[kb.attr_knowledge_base_arn]))  # fmt: skip
        fn.add_to_role_policy(
            iam.PolicyStatement(actions=["bedrock:InvokeModel"], resources=[gen_arn])
        )

        cdk.CfnOutput(self, "KnowledgeBaseId", value=kb.attr_knowledge_base_id)
        cdk.CfnOutput(self, "DataSourceId", value=source.attr_data_source_id)
        cdk.CfnOutput(self, "DocsBucket", value=docs.bucket_name)


def synth(
    settings: RagSettings | None = None, account: str = "123456789012", region: str = "us-east-1"
) -> dict:
    """Synthesise offline and return the CloudFormation template as a dict (no network, no credentials)."""
    app = cdk.App()
    stack = BedrockRagStack(
        app, "BedrockRag", settings=settings, env=cdk.Environment(account=account, region=region)
    )
    return app.synth().get_stack_by_name(stack.stack_name).template
