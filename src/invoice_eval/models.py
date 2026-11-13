from typing import Any, Literal
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, model_validator

LABELS = ["INTENT_CLASSIFICATION", "ENTITY_RESOLUTION", "FIELD_FABRICATION", "AMOUNT_BASIS", "AMOUNT_CALCULATION", "PRODUCT_MATCH", "TOOL_ARGUMENT_ERROR", "MULTI_ITEM_OMISSION", "CONTEXT_UPDATE", "DUPLICATE_CONTROL", "CONFIRMATION_BYPASS", "TOOL_ERROR_HANDLING", "CALLBACK_MISINTERPRETATION", "INSUFFICIENT_EVIDENCE"]

class InputModel(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

class ToolCall(InputModel):
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    attempt: int = Field(default=1, ge=1)

class Event(InputModel):
    event_id: str = Field(min_length=1)
    seq: int = Field(ge=1)
    step_id: str
    task_id: str
    trace_id: str
    module: str
    action: str
    step_status: Literal["PENDING", "RUNNING", "WAITING_USER", "WAITING_CALLBACK", "SUCCEEDED", "FAILED", "SKIPPED", "CANCELED", "TIMED_OUT"]
    started_at: str
    finished_at: str
    duration_ms: int = Field(ge=0)
    result_code: Literal["E100", "E200", "E400", "E500", "E600", "E700", "E800"]
    error_code: str | None
    error_message: str | None
    retry_count: int = Field(ge=0)
    observed_input: dict[str, Any]
    observed_output: dict[str, Any]
    tool_calls: list[ToolCall]

    @model_validator(mode="after")
    def timestamps(self):
        start, end = datetime.fromisoformat(self.started_at), datetime.fromisoformat(self.finished_at)
        if start.tzinfo is None or end.tzinfo is None or end < start:
            raise ValueError("event timestamps must be ordered with timezone")
        return self

class Run(InputModel):
    run_id: str = Field(min_length=1)
    agent_version: str
    task_id: str
    trace_id: str
    events: list[Event] = Field(min_length=1)
    final_state: str
    invoice_created: bool | None = Field(default=None, description="输入报告的出票结果；属于离线记录，不代表评测器已独立查询外部服务。")
    resource_usage: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_events(self):
        for key in ("event_id", "seq"):
            values = [getattr(e, key) for e in self.events]
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {key} in run {self.run_id}")
        if any(e.task_id != self.task_id or e.trace_id != self.trace_id for e in self.events):
            raise ValueError("event task_id/trace_id does not belong to run")
        return self

class Case(InputModel):
    case_id: str = Field(min_length=1)
    user_request: str
    context: dict[str, Any]
    reference_result: dict[str, Any]
    runs: list[Run] = Field(min_length=2, max_length=2)
    scenario: str = ""
    expected_behaviors: list[str] = Field(default_factory=list, description="计划覆盖的行为 ID；不作为实际触发或通过的证据。见 behavior-catalog 命令。")

    @model_validator(mode="after")
    def behavior_ids(self):
        import json
        from .resources import BEHAVIORS
        known = {b["id"] for b in json.loads(BEHAVIORS.read_text(encoding="utf-8"))["behaviors"]}
        if len(set(self.expected_behaviors)) != len(self.expected_behaviors):
            raise ValueError("duplicate expected_behaviors")
        if set(self.expected_behaviors) - known:
            raise ValueError(f"unknown expected_behaviors: {sorted(set(self.expected_behaviors) - known)}")
        return self

class FieldValue(BaseModel):
    value: Any
    source_path: str
    source_event_id: str | None
    known_at_seq: int
    supersedes: list[dict] = Field(default_factory=list)

class Finding(BaseModel):
    finding_id: str
    rule_id: str
    rule_version: str = "1.0"
    status: Literal["pass", "fail", "unknown", "not_applicable"] = "fail"
    label: str
    severity: Literal["business", "risk", "unknown"] = "business"
    first_fault_event_id: str | None
    evidence_event_ids: list[str]
    field_paths: list[str]
    field_name: str = ""
    expected: Any
    observed: Any
    explanation: str
    origin_finding_id: str | None = None
    causal_group_id: str

class StageResult(BaseModel):
    status: Literal["success", "failed", "skipped", "warning"]
    payload_valid: bool
    required_for_decision: bool
    reason: str

class JudgeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["success", "failed", "skipped"]
    question_id: str
    verdict: Literal["pass", "fail", "unknown"]
    label: str | None
    first_fault_event_id: str | None
    evidence_event_ids: list[str]
    field_paths: list[str]
    explanation: str = Field(min_length=1)
    missing_evidence: list[str]

class JudgeCause(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    first_fault_event_id: str | None
    evidence_event_ids: list[str]
    field_paths: list[str]
    explanation: str = Field(min_length=1)

class AuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["success", "failed", "skipped"]
    question_id: str
    verdict: Literal["pass", "fail", "unknown"]
    causes: list[JudgeCause]
    evidence_event_ids: list[str]
    field_paths: list[str]
    explanation: str = Field(min_length=1)
    missing_evidence: list[str]
