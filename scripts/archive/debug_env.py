import sys
import os

print(f"Executable: {sys.executable}")
print("Sys Path:")
for p in sys.path:
    print(p)

try:
    import edgartools
    print(f"Edgartools imported from: {edgartools.__file__}")
except ImportError as e:
    print(f"Import Failed: {e}")
