"""Agent queue — one queue per agent, dispatched one item at a time.

Stage 1 (business logic) packages pending work into agent-ready items; stage 2
(this package) hands them over one at a time, only while the agent is quiet.
"""
