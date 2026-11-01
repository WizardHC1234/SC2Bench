"""The knowledge audit may only carry explicitly explained gaps."""

import scripts.audit_sc2_knowledge as audit


def test_knowledge_audit_has_no_unexplained_gaps():
    allowed = audit._allowlist()
    unexpected = [item for item in audit.collect_issues() if item not in allowed]
    assert unexpected == []
