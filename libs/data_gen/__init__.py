"""Seeded synthetic data generators (no real or private data)."""

from .knowledge_base import Document, KnowledgeBase, QAItem, generate_knowledge_base

__all__ = ["Document", "KnowledgeBase", "QAItem", "generate_knowledge_base"]
