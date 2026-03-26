"""
Test that WeasyPrint can be imported and render a minimal PDF.
Run on Toolforge via scripts/debug/test_weasyprint.sh.
"""
import sys

print(f"Python: {sys.executable}")

try:
    import weasyprint
    print(f"WeasyPrint: {weasyprint.__version__}")
except Exception as e:
    print(f"FAIL import weasyprint: {e}")
    sys.exit(1)

try:
    pdf = weasyprint.HTML(string="<h1>Test</h1>").write_pdf()
    print(f"PDF render: OK ({len(pdf)} bytes)")
except Exception as e:
    print(f"FAIL render: {e}")
    sys.exit(1)

print("All OK")
