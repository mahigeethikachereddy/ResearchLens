"""Render an existing research result without making another model request."""
import html
import io
from pathlib import Path
import re

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak

HEADINGS = {'abstract', 'introduction', 'source and method', 'findings', 'discussion', 'limitations', 'conclusion', 'references'}


def render_pdf(result):
    fonts = Path(reportlab.__file__).parent / 'fonts'
    for name, filename in [('ResearchBody', 'Vera.ttf'), ('ResearchBold', 'VeraBd.ttf')]:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(fonts / filename)))
    body = ParagraphStyle('Body', fontName='ResearchBody', fontSize=11, leading=18, spaceAfter=11, alignment=TA_JUSTIFY, splitLongWords=True)
    heading = ParagraphStyle('Heading', parent=body, fontName='ResearchBold', fontSize=14, leading=20, spaceBefore=17, spaceAfter=10, keepWithNext=True, alignment=0)
    title = ParagraphStyle('Title', parent=heading, fontSize=23, leading=30, spaceAfter=18)
    meta = ParagraphStyle('Metadata', parent=body, fontSize=9, leading=14, textColor=colors.HexColor('#657080'), alignment=0)
    safe = lambda value: html.escape(str(value))
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=54, leftMargin=54, topMargin=60, bottomMargin=60, title=result.title, author='ResearchLens')
    story = [Paragraph('RESEARCHLENS / SOURCE-BASED RESEARCH REPORT', meta), Spacer(1, 14), Paragraph(safe(result.title), title), Paragraph(f'Mode: {safe(result.mode)} | Retrieved: {safe(result.retrieved_at)}<br/>Model: {safe(result.model or "none")} | Source text truncated: {result.truncated}', meta), Spacer(1, 16)]
    paragraph = []
    def flush():
        if paragraph:
            story.append(Paragraph(safe(' '.join(paragraph)), body))
            paragraph.clear()
    for line in result.summary.splitlines():
        clean = re.sub(r'^\s*#{1,6}\s*', '', line).strip().strip('*')
        if clean.casefold() in HEADINGS:
            flush(); story.append(Paragraph(safe(clean), heading))
        elif not line.strip():
            flush()
        else:
            paragraph.append(line.strip())
    flush()
    story.extend([Paragraph('Source reference', heading), Paragraph('[1] ' + safe(result.title) + '. ' + safe(result.url) + '. Accessed ' + safe(result.retrieved_at) + '.', body), Paragraph('AI-assisted synthesis of one supplied page. This is not original empirical research or a multi-source literature review. Verify important claims against the source.', meta), PageBreak(), Paragraph('Appendix: extracted source evidence', heading)])
    for line in result.extracted_text.splitlines():
        if line.strip(): story.append(Paragraph(safe(line), body))
    def footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor('#e3d5dc')); canvas.line(54, 42, A4[0]-54, 42)
        canvas.setFont('ResearchBody', 8); canvas.setFillColor(colors.HexColor('#657080'))
        canvas.drawString(54, 29, 'ResearchLens | Source-based research')
        canvas.drawRightString(A4[0]-54, 29, str(document.page))
        canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
