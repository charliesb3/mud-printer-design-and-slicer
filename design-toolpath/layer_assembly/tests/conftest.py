import os
import sys

# make the `layer_assembly` package importable (its parent: design-toolpath/)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
