from edgar import set_identity, Company
import os

# Use the identify from config or env
identity = os.environ.get("SEC_IDENTITY", "Kashif Shahzad kashif@example.com")
set_identity(identity)

print(f"Identity set to: {identity}")
print("Fetching MSFT 10-K...")

try:
    company = Company("MSFT")
    filings = company.get_filings(form="10-K")
    if filings:
        latest = filings[0]
        print(f"Found filing: {latest.filing_date}")
        
        # Test generic text extraction
        # edgartools often provides .markdown or .text()
        # Let's try to inspect what's available
        print("Attempting to access markdown content...")
        
        # Note: Accessing the text usually triggers a download
        md_attr = latest.markdown
        print(f"Type of .markdown: {type(md_attr)}")
        
        if callable(md_attr):
             md = md_attr()
        else:
             md = md_attr
             
        if md:
            print(f"Markdown extracted! Length: {len(md)}")
            print("First 500 chars:")
            print(md[:500])
        else:
            print("Markdown property returned None.")
            
    else:
        print("No 10-K found.")
except Exception as e:
    print(f"Error: {e}")
