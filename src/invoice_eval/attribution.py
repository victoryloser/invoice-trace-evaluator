from .models import Finding

def group_findings(findings, run):
    order = {e.event_id: e.seq for e in run.events}
    groups = {}
    for finding in findings:
        if finding.status != "fail":
            continue
        groups.setdefault(finding.causal_group_id, []).append(finding)
    result = []
    for group_id, members in groups.items():
        members.sort(key=lambda f: order.get(f.first_fault_event_id, float("inf")))
        root = members[0]
        refs = sorted({x for f in members for x in f.evidence_event_ids}, key=order.__getitem__)
        result.append({"group_id": group_id, "root": root, "members": members, "evidence_event_ids": refs})
    return sorted(result, key=lambda g: order.get(g["root"].first_fault_event_id, float("inf")))
