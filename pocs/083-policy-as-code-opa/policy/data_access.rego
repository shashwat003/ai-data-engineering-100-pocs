# RBAC + ABAC data access policy. Default deny; explicit deny beats allow.
package dataaccess

import rego.v1

default allow := false

levels := {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}

# role -> actions it may perform (RBAC)
role_actions := {
	"analyst": {"read"},
	"engineer": {"read", "write"},
	"steward": {"read", "write", "grant"},
	"auditor": {"read"},
}

pii_purposes := {"fraud_investigation", "customer_support", "regulatory_audit"}

# Fail-closed style: each check is a positive helper and the deny is `not helper`, because a
# comparison on a missing attribute is undefined and would silently skip the deny (fail open).
default user_level := -1

user_level := levels[input.user.clearance]

default res_level := 99

res_level := levels[input.resource.classification]

region_ok if input.resource.region == input.user.region

dept_ok if input.resource.owner_dept == input.user.department

purpose_ok if input.context.purpose in pii_purposes

mfa_ok if input.context.mfa == true

user_active if input.user.active == true

known_class if levels[input.resource.classification]

pii_known_false if input.resource.pii == false

is_pii if not pii_known_false

# ---- explicit denies (any one wins) ----
deny_reasons contains "user_inactive" if not user_active

deny_reasons contains "unknown_classification" if not known_class

deny_reasons contains "region_mismatch" if {
	not region_ok
	not input.user.cross_region_approved == true
}

deny_reasons contains "clearance_too_low" if res_level > user_level

deny_reasons contains "pii_requires_purpose" if {
	is_pii
	not purpose_ok
}

deny_reasons contains "mfa_required" if {
	res_level >= levels.confidential
	not mfa_ok
}

deny_reasons contains "auditors_are_read_only" if {
	"auditor" in input.user.roles
	input.action != "read"
}

deny_reasons contains "write_outside_own_department" if {
	input.action in {"write", "grant"}
	not dept_ok
}

# ---- RBAC allow ----
rbac_ok if {
	some role in input.user.roles
	input.action in role_actions[role]
}

allow if {
	rbac_ok
	count(deny_reasons) == 0
}

# no matching role is a reason too, so every denial is explained
reasons := deny_reasons | {"no_role_grants_action"} if not rbac_ok

reasons := deny_reasons if rbac_ok

decision := {"allow": allow, "reasons": sorted_reasons}

sorted_reasons := sort([r | some r in reasons])
