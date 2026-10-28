"""Classical sentiment classifiers and a prompt-based SLM adapter (the SLM arm is optional and unverified here)."""

from __future__ import annotations

import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import make_pipeline

from .data import LABELS

POS_W = {"great", "love", "excellent", "happy", "perfect", "good", "exactly", "again", "fast"}
NEG_W = {
    "terrible",
    "disappointed",
    "waste",
    "broken",
    "stopped",
    "bad",
    "poor",
    "not",
    "never",
    "claims",
}


class Lexicon:
    """Hand-made polarity word counts: the zero-training baseline."""

    def predict(self, texts: list[str]) -> list[str]:
        out = []
        for t in texts:
            w = re.findall(r"[a-z]+", t.lower())
            s = sum(x in POS_W for x in w) - sum(x in NEG_W for x in w)
            out.append("positive" if s > 0 else "negative" if s < 0 else "neutral")
        return out


class Sk:
    def __init__(self, clf, ngram=(1, 1)):
        self.pipe = make_pipeline(TfidfVectorizer(ngram_range=ngram, lowercase=True), clf)

    def fit(self, texts, labels):
        self.pipe.fit(texts, labels)
        return self

    def predict(self, texts):
        return list(self.pipe.predict(texts))


def classical_models(seed: int = 0) -> dict:
    return {
        "lexicon": Lexicon(),
        "naive_bayes_unigram": Sk(MultinomialNB()),
        "logreg_unigram": Sk(LogisticRegression(max_iter=1000, random_state=seed)),
        "logreg_bigram": Sk(LogisticRegression(max_iter=1000, random_state=seed), ngram=(1, 2)),
    }


PROMPT = "Classify the sentiment of the review as exactly one word: positive, negative or neutral.\nReview: {text}\nAnswer:"


class PromptSlm:
    """Zero-shot sentiment via any LLMClient. Output is parsed strictly; anything unparsable counts as `neutral` and is tallied."""

    def __init__(self, client, model: str):
        self.client, self.model, self.unparsable = client, model, 0

    def predict(self, texts):
        out = []
        for t in texts:
            r = self.client.generate(
                PROMPT.format(text=t), model=self.model, temperature=0.0, max_tokens=4
            )
            word = re.findall(r"[a-z]+", r.text.lower())[:1]
            if word and word[0] in LABELS:
                out.append(word[0])
            else:
                self.unparsable += 1
                out.append("neutral")
        return out
