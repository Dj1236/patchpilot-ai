"""Turning a verified run into a GitHub pull request, only after a person approves it.

proposal.py builds a reviewable proposal from a finished run; approval.py
records a person's approval of exactly that proposal; submission.py makes
the GitHub requests, reconciling with read-only checks before every write.
"""
