"""Decoupled visual-item generator for the URBADAPT-HEAT workflow.

Reads persisted pipeline outputs (``outputs_variants/<variant>/<city>/``) and
renders the workflow-narrative figures and tables. No dependency on
``cityheat`` / ``climada`` -- only numpy / pandas / matplotlib.
"""
from .loader import CityOutputs, load_city
from . import figures, style
