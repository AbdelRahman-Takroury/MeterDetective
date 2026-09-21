import pandas as pd
file_path = 'LCL-June2015v2_98.csv'
print(f"Loading data from: {file_path}...")
df = pd.read_csv(file_path, nrows=500000)
print("\n=== (Columns) ===")
print(df.columns.tolist())

print("\n===(Rows, Columns) ===")
print(df.shape)

print("\n=== (Missing Values) ===")
print(df.isnull().sum())
