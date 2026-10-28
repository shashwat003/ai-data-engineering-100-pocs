"""A local mirror of the Bedrock knowledge-base data path, plus an (UNVERIFIED-on-AWS) boto3 client for the real service.

LocalKnowledgeBase: S3 (moto in tests) -> fixed-size token chunks with overlap -> hashing embeddings -> idempotent SQLite
vector store (POC 047) -> top-k retrieval -> extractive "generation" with Bedrock-shaped citations. The generator is an
extractive stand-in, NOT a foundation model, and the embedder is a hashing stand-in, NOT Titan.
AwsKnowledgeBase issues the same calls the deployed Lambda would; it is only exercised against botocore's service-model
validation (Stubber), never against AWS.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from idempotent_vector_ingestion.store import HashingEmbedder, VectorStore

WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class KbSettings:
    max_tokens: int = 300
    overlap_percentage: int = 20
    top_k: int = 5
    embedding_model: str = "amazon.titan-embed-text-v2:0"
    index_name: str = "kb-index"


def settings_from_template(template: dict) -> KbSettings:
    """Read the knobs back out of the synthesised CloudFormation, so the local mirror cannot drift from the stack."""
    res = template["Resources"]
    ds = next(v for v in res.values() if v["Type"] == "AWS::Bedrock::DataSource")
    fixed = ds["Properties"]["VectorIngestionConfiguration"]["ChunkingConfiguration"][
        "FixedSizeChunkingConfiguration"
    ]
    kb = next(v for v in res.values() if v["Type"] == "AWS::Bedrock::KnowledgeBase")
    fn = next(
        v for k, v in res.items() if v["Type"] == "AWS::Lambda::Function" and k.startswith("Query")
    )
    emb = kb["Properties"]["KnowledgeBaseConfiguration"]["VectorKnowledgeBaseConfiguration"][
        "EmbeddingModelArn"
    ]
    emb = emb["Fn::Join"][1][-1] if isinstance(emb, dict) else emb
    return KbSettings(
        max_tokens=int(fixed["MaxTokens"]),
        overlap_percentage=int(fixed["OverlapPercentage"]),
        top_k=int(fn["Properties"]["Environment"]["Variables"]["TOP_K"]),
        embedding_model=emb.rsplit("/", 1)[-1],
        index_name=kb["Properties"]["StorageConfiguration"]["OpensearchServerlessConfiguration"][
            "VectorIndexName"
        ],
    )


def chunk_tokens(text: str, max_tokens: int, overlap_percentage: int) -> list[str]:
    """Whitespace-token windows of `max_tokens` with `overlap_percentage` overlap (Bedrock's tokenizer differs)."""
    if not 1 <= max_tokens or not 0 <= overlap_percentage < 100:
        raise ValueError("max_tokens must be >= 1 and 0 <= overlap_percentage < 100")
    words = text.split()
    if not words:
        return []
    step = max(1, max_tokens - max_tokens * overlap_percentage // 100)
    out, i = [], 0
    while True:
        out.append(" ".join(words[i : i + max_tokens]))
        if i + max_tokens >= len(words):
            return out
        i += step


class LocalKnowledgeBase:
    def __init__(self, settings: KbSettings | None = None, bucket: str = "docs"):
        self.cfg = settings or KbSettings()
        self.bucket = bucket
        self.emb = HashingEmbedder(self.cfg.embedding_model)
        chunker = lambda t: chunk_tokens(t, self.cfg.max_tokens, self.cfg.overlap_percentage)  # noqa: E731
        self.db = sqlite3.connect(":memory:")
        self.store = VectorStore(self.db, self.emb, chunker)

    def ingest_from_s3(self, s3_client, prefix: str = "") -> dict:
        """StartIngestionJob analogue: make the index equal to the bucket's current objects (idempotent)."""
        corpus: dict[str, str] = {}
        for page in s3_client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=prefix
        ):
            for obj in page.get("Contents", []):
                body = s3_client.get_object(Bucket=self.bucket, Key=obj["Key"])["Body"].read()
                try:
                    corpus[f"s3://{self.bucket}/{obj['Key']}"] = body.decode("utf-8")
                except UnicodeDecodeError:
                    continue  # unsupported binary object: skipped, as Bedrock reports it as a failed document
        return self.store.sync(corpus)

    def chunk_text(self, doc_id: str, pos: int) -> str:
        return self.db.execute(
            "SELECT v.text FROM doc_chunks d JOIN vectors v ON v.key = d.key WHERE d.doc_id=? AND d.pos=?",
            (doc_id, pos),
        ).fetchone()[0]

    def retrieve(self, query: str, k: int | None = None) -> list[dict]:
        hits = self.store.search(query, k or self.cfg.top_k)
        return [
            {
                "content": {"text": self.chunk_text(d, p)},
                "location": {"type": "S3", "s3Location": {"uri": d}},
                "score": s,
            }
            for d, p, s in hits
        ]

    def retrieve_and_generate(self, query: str) -> dict:
        refs = self.retrieve(query)
        q = set(WORD.findall(query.lower()))
        best, best_score = "", 0.0
        for r in refs:
            for sent in re.split(r"(?<=[.!?])\s+", r["content"]["text"]):
                toks = set(WORD.findall(sent.lower()))
                score = len(q & toks) / (len(toks) ** 0.5 or 1)
                if score > best_score:
                    best, best_score = sent, score
        text = best or "I could not find that in the knowledge base."
        cites = (
            [
                {
                    "generatedResponsePart": {
                        "textResponsePart": {"text": text, "span": {"start": 0, "end": len(text)}}
                    },
                    "retrievedReferences": refs[:1],
                }
            ]
            if best
            else []
        )
        return {"output": {"text": text}, "citations": cites}


class AwsKnowledgeBase:
    """The real service call. NOT exercised against AWS in this repository (no credentials, no billable resources)."""

    def __init__(self, client, knowledge_base_id: str, model_arn: str, top_k: int = 5):
        self.client, self.kb, self.model, self.top_k = client, knowledge_base_id, model_arn, top_k

    def retrieve(self, query: str) -> dict:
        return self.client.retrieve(
            knowledgeBaseId=self.kb, retrievalQuery={"text": query},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": self.top_k}},
        )  # fmt: skip

    def retrieve_and_generate(self, query: str) -> dict:
        return self.client.retrieve_and_generate(
            input={"text": query},
            retrieveAndGenerateConfiguration={"type": "KNOWLEDGE_BASE", "knowledgeBaseConfiguration": {
                "knowledgeBaseId": self.kb, "modelArn": self.model,
                "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": self.top_k}}}},
        )  # fmt: skip
