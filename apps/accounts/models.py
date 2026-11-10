import unicodedata

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower


class UserType(models.TextChoices):
    STUDENT = "student", "Aluno"
    TEACHER = "teacher", "Professor"
    ADMIN = "admin", "Administrador"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email: str, password: str, **extra_fields) -> "User":
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str, **extra_fields) -> "User":
        extra_fields["type"] = UserType.ADMIN
        extra_fields.setdefault("full_name", "Administrador")
        extra_fields["is_staff"] = True
        extra_fields["is_superuser"] = True
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    full_name = models.CharField(max_length=150)
    email = models.EmailField(max_length=254, unique=True)
    type = models.CharField(max_length=10, choices=UserType.choices)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name", "type"]

    objects = UserManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("email"), name="user_email_unique_ignoring_case"),
        ]

    def __str__(self) -> str:
        return self.email


class Institute(models.Model):
    name = models.CharField(max_length=150, unique=True)

    def __str__(self) -> str:
        return self.name


COURSE_WITH_TEACHERS_MESSAGE = (
    "Este curso não pode ser excluído porque há professores vinculados a ele."
)


def teachers_of(courses) -> models.QuerySet["Teacher"]:
    return Teacher.objects.filter(courses__in=courses).select_related("user").distinct()


def ensure_courses_have_no_teachers(courses) -> None:
    """RN17. `Student.course` is already protected by its foreign key, but `Teacher.courses` is a
    many-to-many, which Django would delete silently, so the rule is checked here (ADR 0010)."""
    teachers = list(teachers_of(courses))
    if teachers:
        raise models.ProtectedError(COURSE_WITH_TEACHERS_MESSAGE, teachers)


class CourseQuerySet(models.QuerySet):
    def delete(self):
        ensure_courses_have_no_teachers(self)
        return super().delete()


class Course(models.Model):
    objects = CourseQuerySet.as_manager()

    institute = models.ForeignKey(Institute, on_delete=models.PROTECT, related_name="courses")
    name = models.CharField(max_length=150)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["institute", "name"], name="course_name_unique_per_institute"
            ),
        ]

    def delete(self, *args, **kwargs):
        ensure_courses_have_no_teachers([self])
        return super().delete(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


AREA_IN_USE_MESSAGE = (
    "Esta área de interesse não pode ser excluída porque há alunos ou professores vinculados a ela."
)


def students_in_areas(areas) -> models.QuerySet["Student"]:
    return Student.objects.filter(areas_of_interest__in=areas).select_related("user").distinct()


def teachers_in_areas(areas) -> models.QuerySet["Teacher"]:
    return Teacher.objects.filter(areas_of_interest__in=areas).select_related("user").distinct()


def ensure_areas_have_no_users(areas) -> None:
    """RN18. Both links are many-to-many, which Django would delete silently, so the rule is
    checked here (ADR 0010). Only students and teachers count: an activity has no area."""
    linked = [*students_in_areas(areas), *teachers_in_areas(areas)]
    if linked:
        raise models.ProtectedError(AREA_IN_USE_MESSAGE, linked)


class AreaOfInterestQuerySet(models.QuerySet):
    def delete(self):
        ensure_areas_have_no_users(self)
        return super().delete()


class AreaOfInterest(models.Model):
    objects = AreaOfInterestQuerySet.as_manager()

    name = models.CharField(max_length=150, unique=True)

    class Meta:
        verbose_name_plural = "areas of interest"

    def delete(self, *args, **kwargs):
        ensure_areas_have_no_users([self])
        return super().delete(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Student(models.Model):
    user = models.OneToOneField(User, on_delete=models.PROTECT, related_name="student")
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="students")
    registration_number = models.CharField(max_length=30, unique=True)
    areas_of_interest = models.ManyToManyField(AreaOfInterest, related_name="students")

    def __str__(self) -> str:
        return self.user.full_name


class Teacher(models.Model):
    user = models.OneToOneField(User, on_delete=models.PROTECT, related_name="teacher")
    registration_number = models.CharField(max_length=30, unique=True)
    is_available = models.BooleanField(default=True)
    courses = models.ManyToManyField(Course, related_name="teachers")
    areas_of_interest = models.ManyToManyField(AreaOfInterest, related_name="teachers")

    def __str__(self) -> str:
        return self.user.full_name


def normalize_title(title: str) -> str:
    decomposed = unicodedata.normalize("NFKD", title)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.casefold().split())


class PublishedArticle(models.Model):
    student = models.ForeignKey(
        Student, null=True, blank=True, on_delete=models.PROTECT, related_name="articles"
    )
    teacher = models.ForeignKey(
        Teacher, null=True, blank=True, on_delete=models.PROTECT, related_name="articles"
    )
    title = models.CharField(max_length=255)
    title_key = models.CharField(max_length=1000, editable=False, default="")
    year = models.PositiveSmallIntegerField()
    url = models.URLField(max_length=500, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(student__isnull=False, teacher__isnull=True)
                | models.Q(student__isnull=True, teacher__isnull=False),
                name="published_article_has_exactly_one_owner",
            ),
            models.UniqueConstraint(
                fields=["student", "title_key", "year"],
                condition=models.Q(student__isnull=False),
                name="published_article_unique_per_student",
            ),
            models.UniqueConstraint(
                fields=["teacher", "title_key", "year"],
                condition=models.Q(teacher__isnull=False),
                name="published_article_unique_per_teacher",
            ),
        ]

    def save(self, *args, **kwargs):
        self.title_key = normalize_title(self.title)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.title
