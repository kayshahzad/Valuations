import pandas as pd

# We can just read the markdown table and convert it to CSV
md_path = '/Users/kashifshahzad/.gemini/antigravity/brain/426b6bda-c00f-4f62-bd7a-2230751ceedc/structured_fields_25.md'
with open(md_path, 'r') as f:
    lines = f.readlines()

# Extract the table lines
table_lines = [l for l in lines if l.strip().startswith('|')]

# Header
headers = [h.strip() for h in table_lines[0].split('|')[1:-1]]

# Data rows (skip the separator row)
data = []
for row in table_lines[2:]:
    cols = [c.strip() for c in row.split('|')[1:-1]]
    data.append(cols)

df = pd.DataFrame(data, columns=headers)
df.to_csv('scratch/structured_fields_25.csv', index=False)
