import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.normpath(os.path.join(HERE, "..", "tastytrade-api-sample"))
for p in (HERE, SAMPLE):
    if p not in sys.path:
        sys.path.insert(0, p)
