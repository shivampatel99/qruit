from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

from app.models import Candidate, Role


def build_recommendation_report(role: Role, candidates: list[Candidate], dest_path: str) -> str:
    """Renders Module 4's final recommendation report as a PDF attachment
    for `report_email` (RP-1/RP-2)."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, f"QRUIT Recommendation Report: {role.title or role.id}", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 8, f"Role status: {role.status.value}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    ranked = sorted(
        (c for c in candidates if c.shortlisted),
        key=lambda c: c.score or 0, reverse=True,
    )
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Shortlisted candidates", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    if not ranked:
        pdf.cell(0, 8, "No candidates were shortlisted for this role.", new_x="LMARGIN", new_y="NEXT")
    for candidate in ranked:
        line = f"{candidate.name or candidate.email or candidate.id} — score: {candidate.score or 'n/a'}"
        if candidate.skip_interview:
            line += " (interview skipped)"
        elif candidate.interview_status:
            line += f" (interview: {candidate.interview_status})"
        pdf.multi_cell(0, 7, line)

    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    pdf.output(dest_path)
    return dest_path
