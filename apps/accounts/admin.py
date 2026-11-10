import logging

from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.db.models import Q
from django.template.response import TemplateResponse

from config.errors import ConflictError

from .deactivation import ALREADY_DEACTIVATED_MESSAGE, deactivate_user
from .models import (
    AreaOfInterest,
    Course,
    Institute,
    User,
    UserType,
    students_in_areas,
    teachers_in_areas,
    teachers_of,
)

logger = logging.getLogger(__name__)


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


class ProfileFilter(admin.SimpleListFilter):
    title = "perfil"
    parameter_name = "profile"

    def lookups(self, request, model_admin):
        return [(UserType.STUDENT, "Aluno"), (UserType.TEACHER, "Professor")]

    def queryset(self, request, queryset):
        return queryset.filter(type=self.value()) if self.value() else queryset


class InstituteFilter(admin.SimpleListFilter):
    title = "instituto"
    parameter_name = "institute"

    def lookups(self, request, model_admin):
        return Institute.objects.order_by("name").values_list("pk", "name")

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        return queryset.filter(
            Q(student__course__institute=self.value()) | Q(teacher__courses__institute=self.value())
        ).distinct()


class CourseFilter(admin.SimpleListFilter):
    title = "curso"
    parameter_name = "course"

    def lookups(self, request, model_admin):
        courses = Course.objects.select_related("institute").order_by("name")
        return [(course.pk, f"{course.name} ({course.institute})") for course in courses]

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        return queryset.filter(
            Q(student__course=self.value()) | Q(teacher__courses=self.value())
        ).distinct()


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """Lists the users and deactivates them through the RN19 cascade (RFADM07, RFADM08).

    The page is read-only: `is_active` cannot be switched by a checkbox, which would skip the
    cascade, and the account data and the permissions are not edited here. Users are created by
    the registration and the administrator by `createsuperuser`.
    """

    list_display = ("full_name", "email", "type", "situation")
    list_filter = (ProfileFilter, InstituteFilter, CourseFilter)
    ordering = ("full_name",)
    fields = ("full_name", "email", "type", "is_active", "created_at", "last_login")
    readonly_fields = fields
    actions = ["deactivate_selected_user"]

    @admin.display(description="Situação", ordering="is_active")
    def situation(self, user: User) -> str:
        return "Ativo" if user.is_active else "Desativado"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Excluir (desativar) usuário")
    def deactivate_selected_user(self, request, queryset):
        users = list(queryset)
        refusal = self._refusal_for(users)
        if refusal:
            self.message_user(request, refusal, messages.ERROR)
            return None
        user = users[0]
        if "post" not in request.POST:
            return self._confirmation_page(request, user)
        try:
            deactivate_user(user)
        except ConflictError as error:
            self.message_user(request, error.detail, messages.ERROR)
        except Exception:
            logger.exception("deactivating user %s failed", user.pk)
            self.message_user(
                request, "Não foi possível desativar o usuário. Nada foi alterado.", messages.ERROR
            )
        else:
            self.message_user(request, f"O usuário {user.email} foi desativado.", messages.SUCCESS)
        return None

    @staticmethod
    def _refusal_for(users: list[User]) -> str | None:
        if len(users) != 1:
            return "Selecione um único usuário para desativar."
        if users[0].type == UserType.ADMIN:
            return "O Administrador não pode ser desativado por aqui."
        if not users[0].is_active:
            return ALREADY_DEACTIVATED_MESSAGE
        return None

    def _confirmation_page(self, request, user: User) -> TemplateResponse:
        context = {
            **self.admin_site.each_context(request),
            "title": "Excluir (desativar) usuário",
            "opts": self.model._meta,
            "media": self.media,
            "user_to_deactivate": user,
            "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
        }
        return TemplateResponse(
            request, "admin/accounts/user/deactivate_confirmation.html", context
        )
