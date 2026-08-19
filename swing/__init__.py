"""Swing-trading track: a separate, non-interfering extension of the
Layer 1-6 systematic strategy framework (see ../layer1_data_strategies.py
etc.) aimed at ~days-to-4-weeks holding periods on a curated, liquid
universe distinct from the Landry System's current holdings.

Nothing here reads from or writes to the Landry System (landry/ package,
LANDRY_SYSTEM_WORKBOOK_*.xlsx) or to the core layer1-6 pipeline's own
outputs (data_cache/, sweep_results*.csv, layer5_holdout.csv,
layer6_portfolio.json, ...). See swing/universe.py for the ticker list and
swing/pipeline.py for the run.
"""
