# Results (POC 055: REAL offline CDK synthesis + checks + local mirror pipeline; S3 MOCKED with moto; nothing deployed or run on AWS)
Synthesised 15 CloudFormation resources offline (Bedrock::DataSource, Bedrock::KnowledgeBase, IAM::Policy, IAM::Role, Lambda::Function, Logs::LogGroup, OpenSearchServerless::AccessPolicy, OpenSearchServerless::Collection, OpenSearchServerless::CollectionIndex, OpenSearchServerless::SecurityPolicy, S3::Bucket, S3::BucketPolicy). Template checks: **0 findings**; cfn-lint errors: **0**. Settings read back from the template: {'max_tokens': 300, 'overlap_percentage': 20, 'top_k': 5, 'embedding_model': 'amazon.titan-embed-text-v2:0', 'index_name': 'kb-index'}.

## Local mirror retrieval (60 synthetic documents, 40 questions; hashing embedder and extractive generator are stand-ins)

| config | chunks | hit@1 | hit@5 | citation correct | answer contains gold | chunks embedded |
|---|---|---|---|---|---|---|
| stack default (300 tokens, 20% overlap) | 60 | 0.425 | 0.775 | 0.425 | 0.400 | 60 |
| small chunks (40 tokens, 0% overlap) | 120 | 0.475 | 0.725 | 0.450 | 0.250 | 102 |
| tiny chunks + overlap (15 tokens, 30% overlap) | 267 | 0.550 | 0.800 | 0.550 | 0.175 | 264 |

## Modelled monthly cost (ASSUMED list prices; why this stack is never deployed by the repository)

| configuration | OpenSearch Serverless USD | Lambda USD | total excl. model tokens USD |
|---|---|---|---|
| standby replicas off | 350.400 | 0.020 | 350.420 |
| standby replicas on | 700.800 | 0.020 | 700.820 |
