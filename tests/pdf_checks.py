"""Check ReportLab PDF structure without assuming a particular embedded font."""
import re


def assert_pdf_integrity(testcase, pdf):
    testcase.assertTrue(pdf.startswith(b"%PDF-"))
    testcase.assertIn(b"%%EOF", pdf[-32:])
    testcase.assertRegex(pdf, rb"/Type\s*/Pages\b")
    testcase.assertRegex(pdf, rb"/Type\s*/Page\b")
    testcase.assertRegex(pdf, rb"/Count\s+[1-9]\d*")
    match = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", pdf)
    testcase.assertIsNotNone(match, "PDF must contain its final cross-reference offset")
    testcase.assertTrue(pdf[int(match.group(1)):].startswith(b"xref"), "Cross-reference offset must point to xref")
