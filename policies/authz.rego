# Agent Fabric baseline authorization policy.
#
# The gateways query POST /v1/data/fabric/authz with the PolicyInput as `input`.
# This package returns the PolicyDecision shape consumed by fabric_common.governance.OPAClient:
#   { allow, reason, rate_limit, rate_window_seconds, redact_keys }
#
# Phase 2 baseline: allow-list driven, fail-closed (default deny). Tighten per org.
package fabric.authz

import rego.v1

# ---- default decision (deny) --------------------------------------------------
default allow := false

# Aggregate decision object returned to the PEP.
# OPAClient reads result.allow / result.rate_limit / result.redact_keys, etc.
allow := decision.allow
reason := decision.reason
rate_limit := decision.rate_limit
rate_window_seconds := decision.rate_window_seconds
redact_keys := decision.redact_keys

# ---- dev allow-list -----------------------------------------------------------
# In dev the gateways send subject.sub == "anonymous" with scope "*".
dev_subject if input.subject.scopes[_] == "*"

# Tools/agents that anyone may call in the dev baseline.
allowed_mcp_tools := {"echo", "reverse", "add", "get-product-by-id"}
allowed_a2a_skills := {"echo", "assist"}

decision := d if {
	input.protocol == "mcp"
	input.action == "mcp.call_tool"
	dev_subject
	allowed_mcp_tools[input.tool]
	d := {
		"allow": true,
		"reason": "dev-allow-mcp",
		"rate_limit": 120,
		"rate_window_seconds": 60,
		"redact_keys": redacted_arg_keys,
	}
}

decision := d if {
	input.protocol == "a2a"
	input.action == "a2a.message_send"
	dev_subject
	allowed_a2a_skills[input.skill]
	d := {
		"allow": true,
		"reason": "dev-allow-a2a",
		"rate_limit": 120,
		"rate_window_seconds": 60,
		"redact_keys": redacted_arg_keys,
	}
}

# Listing tools is always permitted; the gateway filters per-subject separately.
decision := d if {
	input.action == "mcp.list_tools"
	d := {
		"allow": true,
		"reason": "list-allowed",
		"rate_limit": null,
		"rate_window_seconds": 60,
		"redact_keys": [],
	}
}

# Fallthrough deny with an explicit reason.
decision := d if {
	not matched
	d := {
		"allow": false,
		"reason": "no-rule-matched",
		"rate_limit": null,
		"rate_window_seconds": 60,
		"redact_keys": [],
	}
}

matched if {
	input.protocol == "mcp"
	input.action == "mcp.call_tool"
	dev_subject
	allowed_mcp_tools[input.tool]
}

matched if {
	input.protocol == "a2a"
	input.action == "a2a.message_send"
	dev_subject
	allowed_a2a_skills[input.skill]
}

matched if input.action == "mcp.list_tools"

# ---- data-class redaction -----------------------------------------------------
# Any argument flagged by the gateway as carrying sensitive data is redacted in audit.
# `default` keeps the rule defined even when `input.data_classes` is absent — otherwise
# an undefined value here collapses the whole `decision` object to default-deny.
default redacted_arg_keys := []

redacted_arg_keys := input.arg_keys if {
	count(object.get(input, "data_classes", [])) > 0
}
