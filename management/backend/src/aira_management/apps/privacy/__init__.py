"""The privacy notice every console user reads, and the register it is written from (`FRD-625`).

Nothing here imports Django at module level except `models`, `views` and `urls`: the settings
validate their notice mode and language against `schedule` and `texts` before Django exists.
"""
