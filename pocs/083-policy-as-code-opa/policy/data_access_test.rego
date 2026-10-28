package dataaccess_test

import rego.v1

import data.dataaccess

base_user := {
	"active": true, "roles": ["analyst"], "department": "risk", "region": "eu",
	"clearance": "confidential", "cross_region_approved": false,
}

base_res := {"classification": "internal", "owner_dept": "risk", "region": "eu", "pii": false}

ctx := {"purpose": "customer_support", "mfa": true}

test_analyst_reads_internal if {
	dataaccess.allow with input as {"user": base_user, "resource": base_res, "action": "read", "context": ctx}
}

test_analyst_cannot_write if {
	not dataaccess.allow with input as {"user": base_user, "resource": base_res, "action": "write", "context": ctx}
}

test_pii_needs_purpose if {
	r := object.union(base_res, {"pii": true})
	not dataaccess.allow with input as {"user": base_user, "resource": r, "action": "read", "context": {"mfa": true}}
}

test_inactive_user_denied if {
	u := object.union(base_user, {"active": false})
	d := dataaccess.decision with input as {"user": u, "resource": base_res, "action": "read", "context": ctx}
	d.reasons == ["user_inactive"]
}
