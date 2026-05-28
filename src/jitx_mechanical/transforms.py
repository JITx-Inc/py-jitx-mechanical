"""Coordinate transform utilities for package-local to board coordinates."""

from __future__ import annotations

import math

from .models import Point, Pose


def transform_point(pt: Point, pose: Pose) -> Point:
    """Transform a point from package-local to board coordinates.

    1. Apply flip_x (mirror about Y-axis) if set
    2. Rotate by pose.angle degrees counterclockwise
    3. Translate by (pose.x, pose.y)
    """
    px, py = pt.x, pt.y

    if pose.flip_x:
        px = -px

    angle_rad = math.radians(pose.angle)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    rx = px * cos_a - py * sin_a
    ry = px * sin_a + py * cos_a

    return Point(x=rx + pose.x, y=ry + pose.y)


def transform_angle(angle_deg: float, pose: Pose) -> float:
    """Transform an angle from package-local to board coordinates."""
    if pose.flip_x:
        angle_deg = 180.0 - angle_deg
    return (angle_deg + pose.angle) % 360.0
