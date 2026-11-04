from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower


class UserType(models.TextChoices):
    STUDENT = "student", "Aluno"
    TEACHER = "teacher", "Professor"
    ADMIN = "admin", "Administrador"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password, **extra_fields):
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, **extra_fields):
        extra_fields.setdefault("type", UserType.ADMIN)
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


class Course(models.Model):
    institute = models.ForeignKey(Institute, on_delete=models.PROTECT, related_name="courses")
    name = models.CharField(max_length=150)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["institute", "name"], name="course_name_unique_per_institute"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class AreaOfInterest(models.Model):
    name = models.CharField(max_length=150, unique=True)

    class Meta:
        verbose_name_plural = "areas of interest"

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


class PublishedArticle(models.Model):
    student = models.ForeignKey(
        Student, null=True, blank=True, on_delete=models.PROTECT, related_name="articles"
    )
    teacher = models.ForeignKey(
        Teacher, null=True, blank=True, on_delete=models.PROTECT, related_name="articles"
    )
    title = models.CharField(max_length=255)
    year = models.PositiveSmallIntegerField()
    url = models.URLField(max_length=500, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(student__isnull=False, teacher__isnull=True)
                | models.Q(student__isnull=True, teacher__isnull=False),
                name="published_article_has_exactly_one_owner",
            ),
        ]

    def __str__(self) -> str:
        return self.title
