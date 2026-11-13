# Invoice evaluation rules

These definitions describe implemented checks. They are program documentation,
not business records, statutory tax guidance, or a configurable rule engine.
Execution is implemented in state.py, rules.py and decisions.py; editing this
file alone does not change evaluation behavior.

1. Preserve the user's tax-inclusive or tax-exclusive amount basis. Use the
   configured product rate, Decimal arithmetic and half-up rounding to cents.
2. Request clarification for missing or ambiguous buyers, amounts and invoice
   references. Do not treat invented fields as supplied evidence.
3. Require confirmation of the current invoice contents before submitting.
4. Block duplicate-risk submissions. Distinguish agent behavior from tool rejection.
5. Require the original invoice reference for the supported red-invoice workflow.
6. Preserve all requested batch items; detect omissions, merges and duplicates.
7. After a timeout with unknown acceptance, require both a stable idempotency
   key and explicit retry authorization before replaying a submission.
8. Determine invoice creation from explicit service outcomes, not agent assertions.
9. Respect cancellation and subsequent user updates in event order.

Unknown outcomes or missing critical evidence remain review items. Correct
processing and actual invoice creation are separate judgments. The supported
workflow is limited to the documented invoice trace structures.
