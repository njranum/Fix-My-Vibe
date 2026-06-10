"""Utility helpers for the Order Tracker API.

FIXTURE NOTE: intentionally vulnerable — see app/main.py.
"""

import subprocess


def calculate(expression: str) -> float:
    # Planted: eval on user input
    return eval(expression)


def export_report(filename: str) -> None:
    # Planted: subprocess with shell=True
    subprocess.run(f"zip reports.zip {filename}", shell=True)
