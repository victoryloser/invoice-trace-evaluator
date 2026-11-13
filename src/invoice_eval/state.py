"""Temporal facts. reference_result is deliberately absent from entry derivation."""
import copy
import re
from decimal import Decimal, ROUND_HALF_UP
from .loader import digest
from .models import FieldValue

CONTENT_FIELDS = ("buyer_id", "item_id", "invoice_task_type", "amount_value", "amount_basis", "amounts", "line_items", "original_invoice_id")

def money(value):
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def amounts(value, basis, rate):
    value, rate = money(value), Decimal(str(rate))
    if basis == "tax_exclusive":
        exclusive, tax = value, money(value * rate)
        inclusive = exclusive + tax
    elif basis == "tax_inclusive":
        inclusive = value
        exclusive = money(value / (1 + rate))
        tax = inclusive - exclusive
    else:
        raise ValueError(f"unknown amount basis: {basis}")
    return {"exclusive_amount": str(exclusive), "tax_amount": str(tax), "inclusive_amount": str(inclusive)}

def same_amounts(a, b):
    return a is not None and b is not None and set(a) == set(b) and all(money(a[k]) == money(b[k]) for k in a)

def same_value(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(same_value(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same_value(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float, Decimal)) and not isinstance(a, bool) and isinstance(b, (int, float, str, Decimal)):
        return Decimal(str(a)) == Decimal(str(b))
    if isinstance(b, (int, float, Decimal)) and not isinstance(b, bool) and isinstance(a, str):
        return Decimal(a) == Decimal(str(b))
    return a == b

def parse_request(text, context):
    result = {}
    if re.search(r"取消|撤销|作废当前任务", text) and not re.search(r"不(?:要)?取消", text):
        result["intent"] = "CANCEL"
    elif re.search(r"暂缓|暂停|先别开|先不要开", text):
        result["intent"] = "HOLD"
    elif re.search(r"算了|作罢|不要|别(?:开|提交|再|往下|弄|继续)|先放|回头|缓缓|等一等|不开", text) or not re.search(r"开|申请|票|服务费|金额", text):
        result["intent"] = "UNKNOWN"
    else:
        result["intent"] = "ISSUE"
    result["invoice_task_type"] = "RED" if "红票" in text else "BATCH" if "批量" in text else "BLUE"
    buyers = re.findall(r"CUST-[A-Za-z0-9]+", text)
    candidates = context.get("buyer_candidates", [])
    result["buyer_id"] = buyers[-1] if buyers else candidates[0]["buyer_id"] if len(candidates) == 1 else None
    items = context.get("item_candidates", [])
    named = [i for i in items if i["display_name"] in text]
    result["item_id"] = named[0]["item_id"] if named else items[0]["item_id"] if len(items) == 1 else None
    matches = re.findall(r"(不含税|含税)(?:金额)?\s*([\d,]+(?:\.\d+)?)", text)
    if matches:
        basis, value = matches[-1]
        result.update(amount_basis="tax_exclusive" if basis == "不含税" else "tax_inclusive", amount_value=float(value.replace(",", "")))
    else:
        result["amount_basis"] = None
        bare = re.findall(r"金额\s*([\d,]+(?:\.\d+)?)\s*元", text)
        result["amount_value"] = float(bare[-1].replace(",", "")) if bare else None
    result["original_invoice_id"] = context.get("original_invoice_id")
    source = context.get("original_invoice_record") or context.get("existing_draft")
    if source:
        result.update({k: source[k] for k in ("buyer_id", "item_id") if k in source})
        result.update(amount_value=source["exclusive_amount"], amount_basis="tax_exclusive")
    if context.get("conversation_rounds"):
        last = max(context["conversation_rounds"], key=lambda x: x["round"])
        if "exclusive_amount" in last:
            result.update(amount_value=last["exclusive_amount"], amount_basis="tax_exclusive")
    if context.get("line_items"):
        result["line_items"] = copy.deepcopy(context["line_items"])
        result["item_id"] = result["line_items"][0]["item_id"]
        result.update(amount_value=float(sum(money(i["exclusive_amount"]) for i in result["line_items"])), amount_basis="tax_exclusive")
    for key in context.get("required_missing_fields", []):
        result[key] = None
    if context.get("amount_basis_unspecified"):
        result["amount_basis"] = None
    if context.get("buyer_disambiguation_required"):
        result["buyer_id"] = None
    return result

class State:
    def __init__(self, case):
        self.case = case
        self.target = {k: FieldValue(value=v, source_path="context" if k in case.context or case.context.get("conversation_rounds") else "user_request", source_event_id=None, known_at_seq=0) for k, v in parse_request(case.user_request, case.context).items()}
        for key in self.target:
            if key in case.context:
                self.target[key].source_path = f"context.{key}"
        rounds = case.context.get("conversation_rounds", [])
        if rounds:
            ordered = sorted(enumerate(rounds), key=lambda pair: pair[1]["round"])
            index, latest = ordered[-1]
            if "exclusive_amount" in latest:
                field = self.target["amount_value"]
                field.source_path = f"context.conversation_rounds.{index}.exclusive_amount"
                field.supersedes = [FieldValue(value=r["exclusive_amount"], source_path=f"context.conversation_rounds.{i}.exclusive_amount", source_event_id=None, known_at_seq=0).model_dump() for i, r in ordered[:-1]]
        self.actual = {}
        self.confirmed = case.context.get("user_confirmation_recorded", False)
        self.confirmation_source = "context.user_confirmation_recorded" if self.confirmed else None
        self.confirmation_fingerprint = digest(self.values()) if self.confirmed else None
        self.preview = None
        self.preview_event = None
        self.risks = set(case.context.get("risk_flags", []))
        self.callback = None
        self.callback_event = None
        self.submissions = []
        self.updated = set()

    def values(self):
        return {k: v.value for k, v in self.target.items()}

    def expected_amounts(self):
        t = self.values()
        rates = {i["item_id"]: i["configured_rate"] for i in self.case.context.get("item_candidates", [])}
        if t.get("line_items"):
            totals = {k: Decimal(0) for k in ("exclusive_amount", "tax_amount", "inclusive_amount")}
            for line in t["line_items"]:
                if line["item_id"] not in rates:
                    return None
                for k, v in amounts(line["exclusive_amount"], "tax_exclusive", rates[line["item_id"]]).items():
                    totals[k] += Decimal(v)
            return {k: str(v) for k, v in totals.items()}
        if t.get("amount_value") is None or t.get("amount_basis") is None or t.get("item_id") not in rates:
            return None
        return amounts(t["amount_value"], t["amount_basis"], rates[t["item_id"]])

    def before(self, event):
        """Only explicit incoming user data becomes a new target, never agent output."""
        incoming = event.observed_input
        patch = incoming.get("user_update", {})
        if event.step_id == "USER_MESSAGE" and incoming.get("user_request"):
            text = incoming["user_request"]
            parsed = parse_request(text, {})
            explicit = {k:v for k,v in parsed.items() if v is not None and k not in {"intent", "invoice_task_type"}}
            if parsed["intent"] in {"CANCEL", "HOLD"}:
                explicit["intent"] = parsed["intent"]
            elif parsed["intent"] == "UNKNOWN":
                explicit["intent"] = "UNKNOWN"
            revised_amount = re.search(r"(?:改为|改成|调整到|调整为)\s*([\d,]+(?:\.\d+)?)\s*元?", text)
            if revised_amount:
                explicit["amount_value"] = float(revised_amount.group(1).replace(",", ""))
            if "红票" in text or "蓝票" in text or "批量" in text:
                explicit["invoice_task_type"] = parsed["invoice_task_type"]
            patch = {**explicit, **patch}
        for key, value in patch.items():
            old = self.target.get(key)
            source = f"events.{event.event_id}.observed_input." + (f"user_update.{key}" if key in incoming.get("user_update", {}) else "user_request")
            self.target[key] = FieldValue(value=value, source_path=source, source_event_id=event.event_id, known_at_seq=event.seq, supersedes=[old.model_dump()] if old else [])
            if not old or not same_value(old.value, value):
                self.updated.add(key)
                self.confirmed = False
                self.confirmation_fingerprint = None
        if incoming.get("user_confirmation") is True:
            self.confirmed = True
            self.confirmation_source = event.event_id
            self.confirmation_fingerprint = digest(self.preview or self.values())

    def after(self, event):
        out = event.observed_output
        values = out.get("preview", out)
        for key in CONTENT_FIELDS + ("computed_amounts",):
            if key in values:
                old = self.actual.get(key)
                location = f"events.{event.event_id}.observed_output." + ("preview." if "preview" in out else "") + key
                self.actual[key] = FieldValue(value=values[key], source_path=location, source_event_id=event.event_id, known_at_seq=event.seq, supersedes=[old.model_dump()] if old else [])
        if "preview" in out:
            if self.preview and not same_value(self.preview, out["preview"]):
                self.confirmed = False
            self.preview = copy.deepcopy(out["preview"])
            self.preview_event = event.event_id
            if out.get("invoice_confirmed") is False:
                self.confirmed = False
            if self.confirmed:
                self.confirmation_fingerprint = digest(self.preview)
            if event.observed_input.get("user_confirmation") is True:
                self.confirmed = True
                self.confirmation_fingerprint = digest(self.preview)
        self.risks.update(out.get("risk_flags", []))
        if "callback_status" in out:
            self.callback, self.callback_event = out["callback_status"], event.event_id

    def snapshot(self, event):
        return {"event_id": event.event_id, "seq": event.seq, "target": {k: v.model_dump() for k, v in self.target.items()}, "actual": {k: v.model_dump() for k, v in self.actual.items()}, "confirmed": self.confirmed, "confirmation_source": self.confirmation_source, "confirmation_fingerprint": self.confirmation_fingerprint, "risk_flags": sorted(self.risks), "callback": self.callback}
