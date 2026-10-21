"""
Security findings storage for Security Testing Mode, shared across modules.

This is intentionally separate from app/core/audit.py:
- app/core/audit.py records every normal interaction (application-level log).
- app/core/findings.py records only what a student deliberately flags as a
  security test result, with their own judgement attached.

Findings from every module are written to one security_findings.jsonl file,
tagged with a "module" field, so a single Security Assessment export can
cover the whole portfolio (or be filtered down to one module). A finding may
carry optional retrieval evidence (used by RAG-style modules) -- modules that
have no retrieval step simply never pass retrieved_chunks.

record_finding()'s `retrieved_chunks` parameter is really "whatever evidence
list this module's render_inspector displays" -- Module 2 passes a list of
RetrievedChunk, Module 3 (MCP Security) passes a list of
mcp_client.ToolCallRecord. Both shapes can appear in the same list parameter
without a separate code path at call sites: record_finding() tells them
apart by duck-typing each item (a ToolCallRecord has `.tool_name` /
`.expected_authorization`; a RetrievedChunk does not) and buckets them into
independent `rag_evidence` / `mcp_evidence` summaries, so neither evidence
kind is ever mistaken for the other.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

FINDINGS_FILE = Path("security_findings.jsonl")

ASSESSMENT_OPTIONS = ["Blocked", "Partially Successful", "Successful"]


def _summarize_retrieval(retrieved_chunks) -> dict:
    """Build a small, serializable evidence snapshot from retrieval results.

    retrieved_chunks is a list of RetrievedChunk-like objects exposing
    `.chunk` (with document_id/title/classification) and `.similarity` /
    `.authorized`. This only reads metadata -- it never stores restricted
    chunk body text in the findings file, so exported assessments stay safe
    to share even when the underlying retrieval was not authorized.
    """
    if not retrieved_chunks:
        return {"retrieved": []}

    return {
        "retrieved": [
            {
                "document_id": item.chunk.document_id,
                "title": item.chunk.title,
                "classification": item.chunk.classification,
                "similarity": round(item.similarity, 4),
                "authorized": item.authorized,
            }
            for item in retrieved_chunks
        ]
    }


def _is_tool_call_evidence(item) -> bool:
    """True for app.modules.module3_mcp.mcp_client.ToolCallRecord-like
    objects, false for RetrievedChunk-like objects (or anything else) --
    used to split a mixed-looking evidence list without either module
    importing the other's types."""
    return hasattr(item, "tool_name") and hasattr(item, "expected_authorization")


def _summarize_tool_calls(evidence) -> dict:
    """Build a small, serializable evidence snapshot from MCP tool-call
    results. Mirrors _summarize_retrieval's safety rule: the result payload
    of any call that was not authorized is redacted here too, so an
    exported assessment never contains data a tool call should not have
    returned, even if the vulnerable starter server returned it anyway.
    """
    if not evidence:
        return {"tool_calls": []}

    calls = []
    for record in evidence:
        authorized = bool(record.expected_authorization)
        arguments_sent = {
            key: value
            for key, value in (record.arguments_sent or {}).items()
            if key not in ("requester_id", "requester_role")
        }
        calls.append(
            {
                "tool_name": record.tool_name,
                "arguments_sent": arguments_sent,
                "executed": record.executed,
                "expected_authorization": record.expected_authorization,
                "policy_reason": record.policy_reason,
                "result": record.result if authorized else "[redacted -- not authorized]",
            }
        )
    return {"tool_calls": calls}


def record_finding(
    user,
    module_id: str,
    attack_category,
    prompt,
    model_response,
    assessment,
    security_impact,
    tester_notes,
    retrieved_chunks=None,
) -> None:
    evidence_items = retrieved_chunks or []
    rag_items = [item for item in evidence_items if not _is_tool_call_evidence(item)]
    tool_items = [item for item in evidence_items if _is_tool_call_evidence(item)]

    rag_evidence = _summarize_retrieval(rag_items)
    mcp_evidence = _summarize_tool_calls(tool_items)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "module": module_id,
        "user": user.email,
        "role": user.role,
        "attack_category": attack_category,
        "prompt": prompt,
        "model_response": model_response,
        "assessment": assessment,
        "security_impact": security_impact,
        "tester_notes": tester_notes,
        "query_used": prompt,
        "retrieved_document_ids": [r["document_id"] for r in rag_evidence["retrieved"]],
        "classification_observed": sorted({r["classification"] for r in rag_evidence["retrieved"]}),
        "unauthorized_retrieval_observed": any(
            not r["authorized"] for r in rag_evidence["retrieved"]
        ),
        "rag_evidence": rag_evidence,
        "mcp_evidence": mcp_evidence,
        "unauthorized_tool_call_observed": any(
            not call["expected_authorization"] and call["executed"] for call in mcp_evidence["tool_calls"]
        ),
    }
    with FINDINGS_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def load_findings(module_id: str | None = None) -> list:
    if not FINDINGS_FILE.exists():
        return []

    findings = []
    with FINDINGS_FILE.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                findings.append(json.loads(line))

    if module_id is not None:
        findings = [f for f in findings if f.get("module") == module_id]
    return findings


def clear_findings(module_id: str | None = None) -> None:
    """Clear findings. If module_id is given, only that module's findings
    are removed and every other module's findings are preserved."""
    if not FINDINGS_FILE.exists():
        return

    if module_id is None:
        FINDINGS_FILE.unlink()
        return

    remaining = [f for f in load_findings() if f.get("module") != module_id]
    with FINDINGS_FILE.open("w", encoding="utf-8") as handle:
        for finding in remaining:
            handle.write(json.dumps(finding) + "\n")


def summarize(findings: list) -> dict:
    summary = {"total": len(findings)}
    for option in ASSESSMENT_OPTIONS:
        summary[option] = 0
    summary["unauthorized_retrieval_observed"] = 0
    summary["unauthorized_tool_call_observed"] = 0
    for finding in findings:
        assessment = finding.get("assessment")
        if assessment in summary:
            summary[assessment] += 1
        if finding.get("unauthorized_retrieval_observed"):
            summary["unauthorized_retrieval_observed"] += 1
        if finding.get("unauthorized_tool_call_observed"):
            summary["unauthorized_tool_call_observed"] += 1
    return summary


def build_markdown_report(
    findings,
    module_title: str,
    tester_email: str,
    tester_role: str,
    provider_name: str,
    model_name: str,
    reflection_questions: list[str],
) -> str:
    summary = summarize(findings)
    # Module titles like "GlobalTech AI — Prompt Security" already end in
    # "Security"; don't repeat it.
    heading = f"{module_title} Assessment" if module_title.endswith("Security") else f"{module_title} Security Assessment"
    lines = [
        f"# {heading}",
        "",
        f"**Tester:** {tester_email} ({tester_role.upper()})",
        f"**Model / Provider:** {provider_name} — {model_name}",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Testing Summary",
        "",
        f"- Tests Recorded: {summary['total']}",
        f"- Blocked: {summary['Blocked']}",
        f"- Partially Successful: {summary['Partially Successful']}",
        f"- Successful: {summary['Successful']}",
    ]
    if summary["unauthorized_retrieval_observed"]:
        lines.append(
            f"- Findings with unauthorized retrieval observed: {summary['unauthorized_retrieval_observed']}"
        )
    lines.extend(["", "## Findings", ""])

    if not findings:
        lines.append("_No findings were recorded during this session._")
        lines.append("")

    for i, finding in enumerate(findings, start=1):
        lines.extend([
            f"### Finding {i} — {finding.get('attack_category', 'Unknown')}",
            "",
            f"- **Timestamp:** {finding.get('timestamp', '')}",
            f"- **Tester:** {finding.get('user', '')} ({finding.get('role', '')})",
            f"- **Assessment:** {finding.get('assessment', '')}",
        ])

        rag_evidence = finding.get("rag_evidence", {}).get("retrieved", [])
        if rag_evidence:
            lines.extend([
                f"- **Unauthorized retrieval observed:** {finding.get('unauthorized_retrieval_observed', False)}",
                f"- **Classification(s) observed in retrieval:** "
                f"{', '.join(finding.get('classification_observed', [])) or 'none'}",
                "",
                "**Retrieved evidence (metadata only -- body text is never stored here):**",
                "",
                "| Document | Classification | Similarity | Authorized |",
                "|---|---|---|---|",
            ])
            for item in rag_evidence:
                lines.append(
                    f"| {item.get('document_id', '')} | {item.get('classification', '')} | "
                    f"{item.get('similarity', '')} | {item.get('authorized', '')} |"
                )
            lines.append("")

        mcp_evidence = finding.get("mcp_evidence", {}).get("tool_calls", [])
        if mcp_evidence:
            lines.extend([
                f"- **Unauthorized tool execution observed:** "
                f"{finding.get('unauthorized_tool_call_observed', False)}",
                "",
                "**MCP tool-call evidence (result redacted when not authorized):**",
                "",
                "| Tool | Executed | Expected Authorization | Policy Reason |",
                "|---|---|---|---|",
            ])
            for call in mcp_evidence:
                lines.append(
                    f"| {call.get('tool_name', '')} | {call.get('executed', '')} | "
                    f"{call.get('expected_authorization', '')} | {call.get('policy_reason', '')} |"
                )
            lines.append("")

        lines.extend([
            "**Prompt tested:**",
            "",
            "```",
            finding.get("prompt", ""),
            "```",
            "",
            "**Model response:**",
            "",
            "```",
            finding.get("model_response", ""),
            "```",
            "",
            f"**Observed security impact:** {finding.get('security_impact') or '_none recorded_'}",
            "",
            f"**Tester notes:** {finding.get('tester_notes') or '_none_'}",
            "",
        ])

    lines.append("## Security Engineering Reflection")
    lines.append("")
    for i, question in enumerate(reflection_questions, start=1):
        lines.append(f"{i}. {question}")
    lines.append("")

    return "\n".join(lines)


def write_markdown_report(report: str, export_file_name: str) -> Path:
    path = Path(export_file_name)
    path.write_text(report, encoding="utf-8")
    return path
