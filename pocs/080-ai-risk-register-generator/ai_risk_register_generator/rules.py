"""Simplified EU AI Act triage rules. NOT legal advice: a first-pass screening aid for a human reviewer.

Tiers (highest wins): prohibited > high > limited > minimal. Domain keys are a small, explicit subset
of Article 5 (prohibited) and Annex III (high-risk) so every classification is traceable to a rule.
"""

from __future__ import annotations

PROHIBITED_DOMAINS = {
    "emotion_recognition_workplace": "Art. 5(1)(f): emotion inference in the workplace",
    "social_scoring": "Art. 5(1)(c): social scoring",
    "untargeted_face_scraping": "Art. 5(1)(e): untargeted facial-image scraping",
}
HIGH_RISK_DOMAINS = {
    "credit_scoring": "Annex III(5)(b): creditworthiness of natural persons",
    "employment": "Annex III(4): recruitment and worker management",
    "education_scoring": "Annex III(3): education and vocational training",
    "essential_services": "Annex III(5): access to essential services",
    "law_enforcement": "Annex III(6): law enforcement",
    "biometric_id": "Annex III(1): biometrics",
}
# obligations (simplified labels) and the control key that evidences each
HIGH_RISK_OBLIGATIONS = {
    "risk_management": "Risk management system (Art. 9)",
    "data_governance": "Data and data governance (Art. 10)",
    "technical_documentation": "Technical documentation (Art. 11)",
    "logging": "Record-keeping / logging (Art. 12)",
    "human_oversight": "Human oversight (Art. 14)",
    "accuracy_monitoring": "Accuracy, robustness, cybersecurity (Art. 15)",
}
LIMITED_OBLIGATIONS = {
    "disclosure_to_users": "Transparency: tell users they interact with AI (Art. 50)"
}
DEEPFAKE_OBLIGATION = {"content_labelling": "Label synthetic/deepfake content (Art. 50)"}
