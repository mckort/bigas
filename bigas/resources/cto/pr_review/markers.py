"""HTML comment markers embedded in Bigas pull-request comments.

This module stays free of autofix and GitHub-client imports. Heuristics and
the GitHub client both need the markers, and importing each other to share
them crashes the process on boot.
"""

BIGAS_REVIEW_MARKER = "<!-- bigas-ai-review-marker -->"
BIGAS_AUTOFIX_COOLDOWN_MARKER = "<!-- bigas-autofix-cooldown-marker -->"
