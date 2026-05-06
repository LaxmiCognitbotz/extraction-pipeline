import pandas as pd
import sys

def inspect_excel(file_path):
    try:
        df = pd.read_excel(file_path)
        print(f"Columns in {file_path}:")
        for col in df.columns:
            print(f" - {col}")
        print("\nFirst 5 rows:")
        print(df.head().to_string())
    except Exception as e:
        print(f"Error reading {file_path}: {e}")

if __name__ == "__main__":
    inspect_excel(sys.argv[1])
