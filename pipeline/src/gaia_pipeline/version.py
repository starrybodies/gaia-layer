"""Single source for the pipeline version stamped into every provenance record."""

PIPELINE_VERSION = "0.1.0"

# Bump when the numerical output of the pipeline changes for identical inputs.
# Provenance records carry this so a claim can be tied to the exact code that produced it.
ALGORITHM_VERSION = "2026.08.07"
