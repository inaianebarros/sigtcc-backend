from django.contrib import admin

from .models import Course, Institute, Teacher


@admin.register(Institute)
class InstituteAdmin(admin.ModelAdmin):
    """Only a support for the courses: a course is created under an institute (RFADM01)."""

    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("name", "institute")
    list_filter = ("institute",)
    search_fields = ("name",)
    ordering = ("name",)

    def get_deleted_objects(self, objs, request):
        # The collector already reports the students (protected foreign key); the teachers are
        # linked by a many-to-many, so they are added here and the admin shows its "cannot
        # delete" page instead of failing on the model's guard (RN17).
        to_delete, model_count, perms_needed, protected = super().get_deleted_objects(objs, request)
        teachers = Teacher.objects.filter(courses__in=objs).select_related("user").distinct()
        protected.extend(f"Professor: {teacher}" for teacher in teachers)
        return to_delete, model_count, perms_needed, protected
