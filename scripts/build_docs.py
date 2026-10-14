"""Converts the markdown regulation sources in data/docs_src into PDFs in data/docs.

The helpdesk ingests PDFs (like a real institute handbook), so the sample
regulations are kept as markdown for easy editing and rendered to PDF here.
Run: python scripts/build_docs.py
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "docs_src"
OUT = ROOT / "data" / "docs"


def md_to_pdf(src: Path, dest: Path) -> None:
    styles = getSampleStyleSheet()
    story = []
    title = src.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").split(" (")[0]
    for line in src.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("# "):
            story.append(Paragraph(line[2:], styles["Title"]))
        elif line.startswith("## "):
            story.append(Spacer(1, 6))
            story.append(Paragraph(line[3:], styles["Heading2"]))
        else:
            story.append(Paragraph(line, styles["BodyText"]))
    doc = SimpleDocTemplate(str(dest), pagesize=A4, leftMargin=2.2 * cm, rightMargin=2.2 * cm,
                            title=title, author="Sahayak sample regulations")
    doc.build(story)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for md in sorted(SRC.glob("*.md")):
        pdf = OUT / (md.stem + ".pdf")
        md_to_pdf(md, pdf)
        print("built", pdf.relative_to(ROOT))
