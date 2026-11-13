"""Reserve every exact pair before pairing remaining same-label causes."""
def match_causes(expected, observed, event_order=None):
    order = event_order or {}
    def key(cause):
        event = cause.get("first_fault_event_id")
        return (order.get(event, float("inf")), event or "", cause["label"])
    gold = sorted(enumerate(expected), key=lambda p:key(p[1]))
    pred = sorted(enumerate(observed), key=lambda p:key(p[1]))
    used_g, used_p, pairs = set(), set(), []
    for gi, g in gold:
        for pi, p in pred:
            if pi not in used_p and (g["label"],g.get("first_fault_event_id")) == (p["label"],p.get("first_fault_event_id")):
                pairs.append((gi,pi,True)); used_g.add(gi); used_p.add(pi); break
    for gi, g in gold:
        if gi in used_g:
            continue
        for pi,p in pred:
            if pi not in used_p and g["label"] == p["label"]:
                pairs.append((gi,pi,False)); used_g.add(gi); used_p.add(pi); break
    return pairs, [i for i,_ in gold if i not in used_g], [i for i,_ in pred if i not in used_p]
