from django.contrib import admin

from .models import (
    AreaOfInterest,
    Course,
    Institute,
    students_in_areas,
    teachers_in_areas,
    teachers_of,
)


@admin.register(Institute)
class InstituteAdmin(admin.ModelAdmin):
    """Supports the courses: each course is created under an institute (RFADM01)."""

    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("name", "institute")
    list_filter = ("institute",)
    search_fields = ("name",)
    ordering = ("name",)

    def get_deleted_objects(self, objs, request):
        # Teacher.courses is a many-to-many, so the collector does not report its teachers (RN17).
        to_delete, model_count, perms_needed, protected = super().get_deleted_objects(objs, request)
        protected.extend(f"Professor: {teacher}" for teacher in teachers_of(objs))
        return to_delete, model_count, perms_needed, protected


@admin.register(AreaOfInterest)
class AreaOfInterestAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    ordering = ("name",)

    def get_deleted_objects(self, objs, request):
        # Both links are many-to-many, so the collector does not report their users (RN18).
        to_delete, model_count, perms_needed, protected = super().get_deleted_objects(objs, request)
        protected.extend(f"Aluno: {student}" for student in students_in_areas(objs))
        protected.extend(f"Professor: {teacher}" for teacher in teachers_in_areas(objs))
        return to_delete, model_count, perms_needed, protected
