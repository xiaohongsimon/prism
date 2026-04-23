"""Course source adapter.

Treats each online course as a single 'blog-like' signal. Providers (DLAI,
Coursera, etc.) know how to describe a course's metadata; the shared
CourseAdapter turns that into a single RawItem per sync.
"""

from prism.sources.course.base import CourseAdapter, CourseProvider, CourseRef

__all__ = ["CourseAdapter", "CourseProvider", "CourseRef"]
